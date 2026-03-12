"""Storage Stack - S3 and DynamoDB resources"""

from aws_cdk import (
    Stack,
    RemovalPolicy,
    Duration,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_ecr as ecr,
    CfnOutput,
)
from constructs import Construct


class StorageStack(Stack):
    """
    Storage infrastructure stack
    
    Creates:
    - S3 bucket for document storage
    - DynamoDB table for document metadata
    - DynamoDB table for session management
    - ECR repository for Docker images
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create S3 bucket for document storage
        self.document_bucket = s3.Bucket(
            self,
            "DocumentBucket",
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            removal_policy=RemovalPolicy.DESTROY,
            # Enable EventBridge notifications for S3 events
            event_bridge_enabled=True,
            cors=[
                s3.CorsRule(
                    allowed_origins=["*"],  # Allow all origins for presigned URL uploads
                    allowed_methods=[s3.HttpMethods.PUT, s3.HttpMethods.GET, s3.HttpMethods.HEAD],
                    allowed_headers=["*"],
                    exposed_headers=["ETag"],
                    max_age=3600,  # 1 hour in seconds (CORS max_age expects integer, not Duration)
                )
            ],
            lifecycle_rules=[
                s3.LifecycleRule(
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(90),
                        )
                    ],
                    expiration=Duration.days(365),
                )
            ],
        )

        # Create DynamoDB table for document metadata
        self.metadata_table = dynamodb.Table(
            self,
            "DocumentMetadataTable",
            table_name="DocumentMetadata",
            partition_key=dynamodb.Attribute(
                name="document_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            encryption=dynamodb.TableEncryption.AWS_MANAGED,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Add GSI for querying by owner
        self.metadata_table.add_global_secondary_index(
            index_name="OwnerIndex",
            partition_key=dynamodb.Attribute(
                name="owner",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="upload_timestamp",
                type=dynamodb.AttributeType.STRING,
            ),
        )

        # Add GSI for querying by department
        self.metadata_table.add_global_secondary_index(
            index_name="DepartmentIndex",
            partition_key=dynamodb.Attribute(
                name="department",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="upload_timestamp",
                type=dynamodb.AttributeType.STRING,
            ),
        )

        # Create DynamoDB table for session management
        self.session_table = dynamodb.Table(
            self,
            "SessionTable",
            table_name="StreamlitSessions",
            partition_key=dynamodb.Attribute(
                name="session_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Create ECR repository for Streamlit Docker image
        self.ecr_repository = ecr.Repository(
            self,
            "StreamlitEcrRepository",
            repository_name="document-management-streamlit",
            removal_policy=RemovalPolicy.DESTROY,
            image_scan_on_push=True,
        )

        # Outputs
        CfnOutput(
            self,
            "DocumentBucketName",
            value=self.document_bucket.bucket_name,
            description="S3 bucket name for document storage",
            export_name=f"{construct_id}-DocumentBucketName",
        )

        CfnOutput(
            self,
            "DocumentBucketArn",
            value=self.document_bucket.bucket_arn,
            description="S3 bucket ARN",
            export_name=f"{construct_id}-DocumentBucketArn",
        )

        CfnOutput(
            self,
            "MetadataTableName",
            value=self.metadata_table.table_name,
            description="DynamoDB table name for document metadata",
            export_name=f"{construct_id}-MetadataTableName",
        )

        CfnOutput(
            self,
            "MetadataTableArn",
            value=self.metadata_table.table_arn,
            description="DynamoDB table ARN",
            export_name=f"{construct_id}-MetadataTableArn",
        )

        CfnOutput(
            self,
            "SessionTableName",
            value=self.session_table.table_name,
            description="DynamoDB table name for session management",
            export_name=f"{construct_id}-SessionTableName",
        )

        CfnOutput(
            self,
            "SessionTableArn",
            value=self.session_table.table_arn,
            description="Session table ARN",
            export_name=f"{construct_id}-SessionTableArn",
        )

        CfnOutput(
            self,
            "EcrRepositoryName",
            value=self.ecr_repository.repository_name,
            description="ECR repository name",
            export_name=f"{construct_id}-EcrRepositoryName",
        )

        CfnOutput(
            self,
            "EcrRepositoryUri",
            value=self.ecr_repository.repository_uri,
            description="ECR repository URI",
            export_name=f"{construct_id}-EcrRepositoryUri",
        )

        CfnOutput(
            self,
            "EcrRepositoryArn",
            value=self.ecr_repository.repository_arn,
            description="ECR repository ARN",
            export_name=f"{construct_id}-EcrRepositoryArn",
        )
