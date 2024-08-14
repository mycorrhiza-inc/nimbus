# HTTP server
import os
import traceback
import random
from botocore import endpoint
import redis
import boto3

from pathlib import Path
from pydantic import BaseModel
from typing import Optional, Annotated, Any, Dict
from litestar import MediaType, Request, Litestar, Controller, Response, post, get
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.params import Body, Parameter
from litestar.status_codes import HTTP_500_INTERNAL_SERVER_ERROR
import uvicorn
import logging

from typing import List

from urllib.parse import urlparse

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
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")

S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY")
S3_REGION = os.getenv("S3_REGION")

S3_ENDPOINT = os.getenv("S3_ENDPOINT")


class PDFUploadFormData(BaseModel):
    file: bytes


class RunModelData(BaseModel):
    input_file_s3_url: str
    model: Optional[str] = None


class PathUpload(BaseModel):
    path: str


class RequestStatus(BaseModel):
    status: str
    success: str
    request_id: str
    request_check_url: str
    request_check_url_leaf: str
    markdown: Optional[str] = None
    error: Optional[str] = None


logger = logging.getLogger(__name__)

for x in [
    S3_ACCESS_KEY,
    S3_SECRET_KEY,
    S3_REGION,
    S3_ENDPOINT,
]:
    logger.info("Test")
    logger.info("Test 2")
    logger.info(x)
    assert isinstance(x, str)
    assert x != ""
s3_client = boto3.client(
    "s3",
    endpoint_url=S3_ENDPOINT,
    aws_access_key_id=S3_ACCESS_KEY,
    aws_secret_access_key=S3_SECRET_KEY,
    region_name=S3_REGION,
)
redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


def get_status_from_redis(request_id: int) -> dict:
    status = redis_client.hgetall(str(request_id))
    if status is None:
        return {"status": "not_found", "success": str(False)}
    logger.info(type(status))
    return status


def push_to_queue(request_id: int, priority: bool):
    if priority:
        pushkey = REDIS_PRIORITY_QUEUE_KEY
    else:
        pushkey = REDIS_BACKGROUND_QUEUE_KEY
    redis_client.rpush(pushkey, request_id)


def update_status_in_redis(request_id: int, status: Dict[str, str]) -> None:
    redis_client.hmset(str(request_id), status)


def upload_file_to_s3(file, file_name, bucket: Optional[str] = None):
    if bucket is None:
        raise Exception("No default bucket provided")
    s3_client.put_object(Bucket=bucket, Key=file_name, Body=file)
    return generate_s3_uri(file_name=file_name, bucket=bucket)


def generate_s3_uri(
    file_name: str, bucket: Optional[str] = None, s3_endpoint: Optional[str] = None
) -> str:
    if s3_endpoint is None:
        s3_endpoint = S3_ENDPOINT

    if bucket is None:
        bucket = S3_BUCKET_NAME

    # Remove any trailing slashes from the S3 endpoint
    s3_endpoint = s3_endpoint.rstrip("/")

    # Extract the base endpoint (e.g., sfo3.digitaloceanspaces.com)
    base_endpoint = s3_endpoint.split("//")[-1]

    # Construct the S3 URI
    s3_uri = f"https://{bucket}.{base_endpoint}/{file_name}"

    return s3_uri


class SwitchModelRunner(Controller):
    async def run_switch_raw(
        self, s3_url: str, request_id: int, priority: bool, model: str
    ) -> dict:
        shared_dict = {
            "status": "processing",
            "success": str(True),
            "request_id": str(request_id),
            "request_check_url": f"https://marker.kessler.xyz/api/v1/marker/{request_id}",
            "request_check_url_leaf": f"/api/v1/{request_id}",
            "input_files": s3_url,
            "model": model,
            "priority": str(priority),
        }
        # Update Redis with status and S3 URL
        update_status_in_redis(request_id, shared_dict)
        push_to_queue(request_id, priority)
        return shared_dict

    @post(path="/api/v1/run-switch-model")
    async def run_switch_model_from_s3_inputfiles(
        self,
        data: RunModelData,
        priority: bool = True,
    ) -> dict:
        s3_url = data.input_file_s3_url
        request_id = random.randint(100000, 999999)
        extra_info = {model: data.model}
        return await self.run_switch_raw(
            s3_url=s3_url, request_id=request_id, priority=priority, model=data.model
        )

    @get(path="/api/v1/get-all-modeldata")
    async def run_switch_model_from_s3_inputfiles(
        self,
    ) -> List[dict]:
        # TODO : Generate this programatically
        return [
            {
                "name": "scc7a_60_fuel",
                "visualization_url": "https://nimbus.kessler.xyz/scc7a_60_fuel",
                "data_url": "https://nimbus.kessler.xyz/scc7a_60_fuel",
                "date": "10/08/2024",
            }
        ]

    @get(path="/api/v1/{request_id:int}")
    async def get_request_status(
        self,
        request_id: int = Parameter(
            title="Request ID", description="Request id to retrieve"
        ),
    ) -> dict:
        return get_status_from_redis(request_id)

    @post(path="/api/v1/dangerous/clear_queue")
    async def clear_marker_queue(
        self,
    ) -> str:
        redis_client.ltrim(REDIS_PRIORITY_QUEUE_KEY, 0, 0)
        redis_client.ltrim(REDIS_BACKGROUND_QUEUE_KEY, 0, 0)
        return "Success"


def plain_text_exception_handler(request: Request, exc: Exception) -> Response:
    tb = traceback.format_exc()
    status_code = getattr(exc, "status_code", HTTP_500_INTERNAL_SERVER_ERROR)
    return Response(media_type=MediaType.TEXT, content=tb, status_code=status_code)


def start_server():
    port = os.environ.get("MARKER_PORT", 2718)
    port = int(port)
    app = Litestar(
        route_handlers=[SwitchModelRunner],
        exception_handlers={Exception: plain_text_exception_handler},
    )
    run_config = uvicorn.Config(app, port=port, host="0.0.0.0")
    server = uvicorn.Server(run_config)

    # Start background worker in a separate thread

    server.run()


if __name__ == "__main__":
    start_server()
