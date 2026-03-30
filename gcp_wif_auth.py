"""Obtain a GCP ID token (JWT) from AWS using Workload Identity Federation.

Works on EC2 instances with IMDSv2 by sourcing AWS credentials via boto3
instead of relying on the IMDS metadata endpoint directly.
"""

import json
import sys

import boto3
from google.auth import aws as google_auth_aws
from google.auth import impersonated_credentials
from google.auth.transport.requests import Request


DEFAULT_TARGET_AUDIENCE = "https://gtm-graphql-stg.zi-ext.com"


class Boto3CredentialSupplier(google_auth_aws.AwsSecurityCredentialsSupplier):
    """Supplies AWS credentials to google-auth using boto3 (IMDSv2-compatible)."""

    def __init__(self):
        super().__init__()

    def get_aws_region(self, context, request):
        session = boto3.session.Session()
        return session.region_name

    def get_aws_security_credentials(self, context, request):
        session = boto3.session.Session()
        creds = session.get_credentials().get_frozen_credentials()
        return google_auth_aws.AwsSecurityCredentials(
            access_key_id=creds.access_key,
            secret_access_key=creds.secret_key,
            session_token=creds.token,
        )


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "clientLibraryConfig-gtm-graphql.json"
    target_audience = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_TARGET_AUDIENCE

    with open(config_path) as f:
        config = json.load(f)

    supplier = Boto3CredentialSupplier()

    credentials = google_auth_aws.Credentials(
        audience=config["audience"],
        subject_token_type=config["subject_token_type"],
        token_url=config["token_url"],
        aws_security_credentials_supplier=supplier,
    )

    # Wrap with service account impersonation if configured
    impersonation_url = config.get("service_account_impersonation_url")
    if impersonation_url:
        sa_email = impersonation_url.split("/serviceAccounts/")[1].split(":")[0]
        credentials = impersonated_credentials.Credentials(
            source_credentials=credentials,
            target_principal=sa_email,
            target_scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )

        # Generate an ID token (JWT) for the target audience
        id_creds = impersonated_credentials.IDTokenCredentials(
            target_credentials=credentials,
            target_audience=target_audience,
            include_email=True,
        )
        id_creds.refresh(Request())

        print("ID token (JWT):", id_creds.token)
        print("Expiry:", id_creds.expiry)
    else:
        credentials.refresh(Request())
        print("Access token:", credentials.token)
        print("Expiry:", credentials.expiry)


if __name__ == "__main__":
    main()
