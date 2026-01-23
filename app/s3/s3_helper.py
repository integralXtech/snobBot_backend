import os
from botocore.exceptions import ClientError
from app.s3.s3_client import s3_client, BUCKET_NAME, AWS_REGION


def upload_file_to_s3(file_bytes: bytes, s3_key: str, content_type: str = "application/octet-stream"):
    """
    Upload bytes to S3 at the given key. Returns dict with status and url/message.
    s3_key should include any desired folders, e.g. "user-id/files/my.pdf"
    """
    try:
        if not isinstance(s3_key, str):
            raise ValueError(f"s3_key must be a string, got {type(s3_key)} -> {s3_key}")

        s3_client.put_object(
            Bucket=BUCKET_NAME,
            Key=s3_key,
            Body=file_bytes,
            ContentType=content_type,
        )

        return {"status": "success", "url": f"s3://{BUCKET_NAME}/{s3_key}"}

    except ClientError as exc:
        return {"status": "error", "message": str(exc)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}


def list_files_in_s3(prefix: str):
    """
    List all files under a given prefix.
    Always returns list of dicts with {"key": str}.
    (URLs should be generated using generate_presigned_url for security.)
    """
    try:
        response = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=prefix)
        if "Contents" not in response:
            return []
        return [{"key": obj["Key"]} for obj in response["Contents"]]
    except ClientError as e:
        return {"status": "error", "message": str(e)}


def get_file_from_s3(key: str) -> bytes:
    """
    Fetch a file from S3 and return its raw bytes.
    Key must be a string like: "user_id/files/my.pdf"
    """
    if isinstance(key, dict):
        raise ValueError(f"S3 key must be a string, but got dict instead: {key}")

    if not isinstance(key, str):
        raise ValueError(f"S3 key must be a string, got {type(key)} -> {key}")

    try:
        response = s3_client.get_object(Bucket=BUCKET_NAME, Key=key)
        return response["Body"].read()
    except ClientError as exc:
        raise Exception(f"Failed to fetch file from S3: {exc}")
    except Exception as exc:
        raise Exception(f"Unexpected error fetching file from S3: {exc}")


def generate_presigned_url(key: str, expires_in: int = 3600) -> str:
    """
    Generate a presigned URL for an S3 object.
    Default expiration: 1 hour (3600 seconds).
    """
    try:
        url = s3_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": BUCKET_NAME, "Key": key},
            ExpiresIn=expires_in
        )
        return url
    except ClientError as e:
        raise Exception(f"Error generating presigned URL: {e}")
    
def delete_file_from_s3(key: str) -> dict:
    try:
        # Check if the file exists first
        s3_client.head_object(Bucket=BUCKET_NAME, Key=key)

        # If no exception, file exists → delete it
        s3_client.delete_object(Bucket=BUCKET_NAME, Key=key)
        return {"status": "success", "key": key, "deleted": True}

    except ClientError as e:
        error_code = e.response["Error"]["Code"]

        if error_code == "404":
            return {"status": "error", "message": f"File not found: {key}"}
        else:
            return {"status": "error", "message": str(e)}