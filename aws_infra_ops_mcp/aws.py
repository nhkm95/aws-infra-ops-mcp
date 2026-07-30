"""AWS SDK client construction.

Keeping this factory separate lets business logic receive a stubbed client in
tests while production uses Boto3's standard configuration and credential
provider chains.
"""

from typing import Any

import boto3


def create_ec2_client() -> Any:
    """Create an EC2 client from Boto3's standard session configuration."""
    return boto3.client("ec2")


def create_logs_client() -> Any:
    """Create a CloudWatch Logs client from Boto3's standard configuration."""
    return boto3.client("logs")


def create_ssm_client() -> Any:
    """Create an SSM client from Boto3's standard session configuration."""
    return boto3.client("ssm")
