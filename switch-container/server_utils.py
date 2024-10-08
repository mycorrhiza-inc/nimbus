import os
import shutil
import traceback
import time
import redis
import boto3
import threading

from pathlib import Path
from pydantic import BaseModel
from typing import Optional, Annotated, Any, Dict, Tuple
import logging

from run_switch import run_switch_model


import sys

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, stream=sys.stderr)


class BaseMarkerCliInput(BaseModel):
    in_folder: str
    out_folder: str
    chunk_idx: int = 0
    num_chunks: int = 1
    max_pdfs: Optional[int] = None
    min_length: Optional[int] = None
    metadata_file: Optional[str] = None


class PDFUploadFormData(BaseModel):
    file: bytes


class URLUpload(BaseModel):
    url: str


class PathUpload(BaseModel):
    path: str


NIMBUS_DIR = Path("/nimbus")
SWITCHRUNS_DIR = NIMBUS_DIR / Path("switchruns")


class RequestStatus(BaseModel):
    status: str
    success: str
    request_id: str
    request_check_url: str
    request_check_url_leaf: str
    markdown: Optional[str] = None
    error: Optional[str] = None


REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = 6379
REDIS_STATUS_KEY = os.getenv("REDIS_STATUS_KEY", "request_status")
REDIS_BACKGROUND_QUEUE_KEY = os.getenv(
    "REDIS_BACKGROUND_QUEUE_KEY", "request_queue_background"
)
REDIS_PRIORITY_QUEUE_KEY = os.getenv(
    "REDIS_PRIORITY_QUEUE_KEY", "request_queue_priority"
)
REDIS_S3_URLS_KEY = os.getenv("REDIS_S3_URLS_KEY", "request_s3_urls")

S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_REGION = os.getenv("S3_REGION")
S3_ENDPOINT = os.getenv("S3_ENDPOINT")


S3_OUTPUT_BUCKET_NAME = os.getenv("S3_OUTPUT_BUCKET_NAME")


s3_client = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    region_name=S3_REGION,
)
s3_resource = boto3.resource(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    region_name=S3_REGION,
)
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
from urllib.parse import urlparse


def update_status_in_redis(request_id: int, status: Dict[str, str]) -> None:
    redis_client.hmset(str(request_id), status)


def pop_from_queue() -> Optional[int]:
    # TODO : Clean up code logic
    request_id = redis_client.lpop(REDIS_PRIORITY_QUEUE_KEY)
    if request_id is None:
        request_id = redis_client.lpop(REDIS_BACKGROUND_QUEUE_KEY)
    if request_id is None:
        return None
    if isinstance(request_id, int):
        return request_id
    if isinstance(request_id, str):
        return int(request_id)
    logger.error(type(request_id))
    raise Exception(
        f"Request id is not string or none and is {type(request_id)} instead."
    )


def parse_s3_uri_to_bucket_and_key(s3_uri: str) -> Tuple[str, str]:
    """
    Parses an S3 URI and creates a boto3 request.

    Args:
        s3_uri (str): The S3 URI to parse.

    Returns:
        dict: A dictionary containing the bucket name and key.
    """
    parsed_url = urlparse(s3_uri)

    # Extract the bucket name from the hostname
    bucket_name = parsed_url.hostname.split(".")[0]

    # Extract the key from the path
    key = parsed_url.path.lstrip("/")

    return (bucket_name, key)


def download_file_from_s3_url(s3_url: str, local_path: Path) -> None:
    s3_bucket, s3_key = parse_s3_uri_to_bucket_and_key(s3_url)
    s3_client.download_file(s3_bucket, s3_key, str(local_path))


def download_folder_from_s3(bucket_name, s3_folder, local_folder):
    if isinstance(local_folder, Path):
        local_folder = str(local_folder)
    logger.info(local_folder)
    # Ensure the local folder exists
    if not os.path.exists(local_folder):
        os.makedirs(local_folder)
    bucket = s3_resource.Bucket(bucket_name)
    # Iterate over the objects in the specified S3 folder
    for obj in bucket.objects.filter(Prefix=s3_folder):
        logger.info(obj)
        # Remove the folder prefix from the object key to get the relative path
        relative_path = os.path.relpath(obj.key, s3_folder)
        local_file_path = os.path.join(local_folder, relative_path)
        logger.info(local_file_path)

        # Ensure the local directory exists
        local_dir = os.path.dirname(local_file_path)

        logger.info(local_dir)
        if not os.path.exists(local_dir):
            os.makedirs(local_dir)

        # Download the file
        try:
            bucket.download_file(obj.key, local_file_path)
        except Exception as e:
            logger.info(
                f"Encountered the following error while downloading file, ignoring and moving on: {e}"
            )
        else:
            logger.info(f"Downloaded {obj.key} to {local_file_path}")


def upload_folder_to_s3(bucket_name, s3_folder, local_folder):

    # Walk through the local folder
    for root, dirs, files in os.walk(local_folder):
        for file in files:
            # Construct the full local path
            local_file_path = os.path.join(root, file)

            # Construct the relative path and then the full S3 path
            relative_path = os.path.relpath(local_file_path, local_folder)
            s3_file_path = os.path.join(s3_folder, relative_path).replace("\\", "/")

            # Upload the file
            s3_client.upload_file(local_file_path, bucket_name, s3_file_path)
            logger.info(
                f"Uploaded {local_file_path} to s3://{bucket_name}/{s3_file_path}"
            )


def process_model_run_from_s3(request_id: int) -> None:
    switch_dir = SWITCHRUNS_DIR / Path(str(request_id))
    input_directory = switch_dir / Path("inputs")
    os.makedirs(switch_dir, exist_ok=True)
    os.makedirs(input_directory, exist_ok=True)
    output_directory = switch_dir / Path("outputs")
    os.makedirs(output_directory, exist_ok=True)

    # Get PDF URL from Redis
    s3_url = redis_client.hget(str(request_id), "input_files")
    if s3_url is None:
        update_status_in_redis(
            request_id,
            {"status": "error", "success": str(False), "error": "No S3 URL found"},
        )
        return None

    s3_input_bucket, s3_input_files_dir = parse_s3_uri_to_bucket_and_key(s3_url)
    download_folder_from_s3(
        bucket_name=s3_input_bucket,
        s3_folder=s3_input_files_dir,
        local_folder=input_directory,
    )
    model_name = redis_client.hget(str(request_id), "model")
    if model_name == "switch":
        shutil.copy(input_directory / Path("modules.txt"), switch_dir)
        shutil.copy(input_directory / Path("options.txt"), switch_dir)
        run_switch_model(str(switch_dir))
    if model_name == "dummy":
        shutil.copy(input_directory / Path("*"), output_directory)

    output_bucket_name = S3_OUTPUT_BUCKET_NAME
    upload_folder_to_s3(
        bucket_name=output_bucket_name,
        s3_folder=str(request_id),
        local_folder=output_directory,
    )
    out_url = (
        f"https://{output_bucket_name}.sfo3.digitaloceanspaces.com/{str(request_id)}"
    )

    update_status_in_redis(
        request_id,
        {
            "status": "complete",
            "success": str(True),
            "output_files": out_url,
        },
    )


def background_worker():
    logger.info("Starting Background Worker")
    while True:
        request_id = pop_from_queue()
        if request_id is not None:
            logger.info(
                f"Beginning to Process model run with request id: {request_id}",
            )
            try:
                process_model_run_from_s3(request_id)
            except Exception as e:
                logger.error(
                    f"Encountered error while running switch model: {request_id}"
                )
                logger.error(e)
                update_status_in_redis(
                    request_id,
                    {
                        "status": "error",
                        "success": str(False),
                        "error": "Error running switch model: " + str(e),
                    },
                )
        else:
            logger.info(
                "Found no switch model to run checking again in 5 seconds.",
            )
            time.sleep(5)


def start_server():
    background_worker()


if __name__ == "__main__":
    start_server()
