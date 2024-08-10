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

from .run_switch import run_switch_model


import sys

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, stream=sys.stderr)


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

s3_client = boto3.client(
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
    # Initialize a session using Amazon S3
    bucket = s3_client.Bucket(bucket_name)

    # Ensure the local folder exists
    if not os.path.exists(local_folder):
        os.makedirs(local_folder)

    # Iterate over the objects in the specified S3 folder
    for obj in bucket.objects.filter(Prefix=s3_folder):
        # Remove the folder prefix from the object key to get the relative path
        relative_path = os.path.relpath(obj.key, s3_folder)
        local_file_path = os.path.join(local_folder, relative_path)

        # Ensure the local directory exists
        local_dir = os.path.dirname(local_file_path)
        if not os.path.exists(local_dir):
            os.makedirs(local_dir)

        # Download the file
        bucket.download_file(obj.key, local_file_path)
        print(f"Downloaded {obj.key} to {local_file_path}")


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
            print(f"Uploaded {local_file_path} to s3://{bucket_name}/{s3_file_path}")


def process_model_run_from_s3(request_id: int) -> None:
    switch_dir = SWITCHRUNS_DIR / Path(str(request_id))
    os.makedirs(switch_dir / Path("inputs"), exist_ok=True)
    input_directory = switch_dir / Path("inputs")
    output_directory = switch_dir / Path("outputs")

    # Get PDF URL from Redis
    s3_url = redis_client.hget(str(request_id), "input_files")
    if s3_url is None:
        update_status_in_redis(
            request_id,
            {"status": "error", "success": str(False), "error": "No S3 URL found"},
        )
        return None

    try:
        s3_input_bucket, s3_input_files_dir = parse_s3_uri_to_bucket_and_key(s3_url)
    except Exception as e:
        logger.error(e)
        update_status_in_redis(
            request_id,
            {
                "status": "error",
                "success": str(False),
                "error": f"No S3 URL could not be parse. {e}",
            },
        )
        return None
    try:
        download_folder_from_s3(
            bucket_name=s3_input_bucket,
            s3_folder=s3_input_files_dir,
            local_folder=input_directory,
        )
    except Exception as e:
        logger.error(
            f"Encountered error while processing {request_id} in getting input file folder from s3"
        )
        logger.error(e)
        update_status_in_redis(
            request_id,
            {
                "status": "error",
                "success": str(False),
                "error": "Error in retreiving files from s3: " + str(e),
            },
        )

    # Now process as normal
    try:
        os.makedirs(output_directory, exist_ok=True)
        run_switch_model(switch_dir)

        update_status_in_redis(
            request_id,
            {
                "status": "complete",
                "success": str(True),
                "output_files": "Not Implemented Yet",
            },
        )
    except Exception as e:
        logger.error(f"Encountered error while processing {request_id} in pdf stage")
        logger.error(e)
        update_status_in_redis(
            request_id,
            {
                "status": "error",
                "success": str(False),
                "error": "Error in pdf processing stage: " + str(e),
            },
        )
    finally:
        # shutil.rmtree(switch_dir)
        pass


def background_worker():
    print("Starting Background Worker", file=sys.stderr)
    while True:
        request_id = pop_from_queue()
        if request_id is not None:
            print(
                f"Beginning to Process model run with request id: {request_id}",
                file=sys.stderr,
            )
            process_model_run_from_s3(request_id)
        else:
            print("Found no switch model to run checking again in 5 seconds.")
            time.sleep(5)


def start_server():
    print("Test output to stdout")
    print("Test output to stderr", file=sys.stderr)
    logger.info("Initializing models and workers.")
    background_worker()


if __name__ == "__main__":
    start_server()
