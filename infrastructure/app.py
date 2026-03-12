#!/usr/bin/env python3
"""AWS CDK application entry point for Document Management System"""

import aws_cdk as cdk
from aws_cdk import aws_s3 as s3, aws_s3_notifications as s3_notifications
from stacks.networking_stack import NetworkingStack
from stacks.storage_stack import StorageStack
from stacks.auth_stack import AuthStack
from stacks.authorization_stack import AuthorizationStack
from stacks.compute_stack import ComputeStack
from stacks.observability_stack import ObservabilityStack
from stacks.ecs_stack import EcsStack

app = cdk.App()

# 1. Networking Stack
networking_stack = NetworkingStack(
    app,
    "DocumentManagement-Networking",
    description="VPC and networking infrastructure for Document Management System",
)

# 2. Storage Stack
storage_stack = StorageStack(
    app,
    "DocumentManagement-Storage",
    description="S3, DynamoDB, and ECR for Document Management System",
)

# 3. Authentication Stack
auth_stack = AuthStack(
    app,
    "DocumentManagement-Auth",
    description="Cognito User Pool for Document Management System",
)

# 4. Authorization Stack
authorization_stack = AuthorizationStack(
    app,
    "DocumentManagement-Authorization",
    description="Verified Permissions and Cedar policies for Document Management System",
)

# 5. Compute Stack - Lambda + API Gateway (depends on: storage, auth, authorization)
compute_stack = ComputeStack(
    app,
    "DocumentManagement-Compute",
    document_bucket=storage_stack.document_bucket,
    metadata_table=storage_stack.metadata_table,
    session_table=storage_stack.session_table,
    user_pool=auth_stack.user_pool,
    user_pool_client=auth_stack.user_pool_client,
    policy_store=authorization_stack.policy_store,
    description="Lambda functions and API Gateway for Document Management System",
)

# 6. Observability Stack
observability_stack = ObservabilityStack(
    app,
    "DocumentManagement-Observability",
    authorizer_function=compute_stack.authorizer_function,
    upload_function=compute_stack.upload_function,
    download_function=compute_stack.download_function,
    list_function=compute_stack.list_function,
    delete_function=compute_stack.delete_function,
    share_function=compute_stack.share_function,
    cleanup_function=compute_stack.cleanup_function,
    api=compute_stack.api,
    metadata_table=storage_stack.metadata_table,
    description="CloudWatch monitoring and alarms for Document Management System",
)

# 7. ECS Stack (depends on: networking, storage, auth, compute)
# Deploy this stack AFTER building and pushing Docker image to ECR
ecs_stack = EcsStack(
    app,
    "DocumentManagement-ECS",
    vpc=networking_stack.vpc,
    ecr_repository=storage_stack.ecr_repository,
    user_pool=auth_stack.user_pool,
    user_pool_client=auth_stack.user_pool_client,
    api=compute_stack.api,
    session_table=storage_stack.session_table,
    description="ECS Fargate service for Streamlit application",
)

app.synth()
