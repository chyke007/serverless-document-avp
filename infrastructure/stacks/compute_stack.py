"""Compute Stack - Lambda functions and API Gateway"""

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_lambda as lambda_,
    aws_iam as iam,
    aws_logs as logs,
    aws_events as events,
    aws_events_targets as targets,
    aws_apigateway as apigateway,
    aws_s3 as s3,
    aws_dynamodb as dynamodb,
    aws_cognito as cognito,
    aws_verifiedpermissions as verifiedpermissions,
    CfnOutput,
    BundlingOptions,
    AssetHashType,
)
from constructs import Construct
from pathlib import Path


class ComputeStack(Stack):
    """
    Compute infrastructure stack
    
    Creates:
    - 8 Lambda functions (authorizer, upload, download, list, delete, share, cleanup, upload_complete)
    - API Gateway REST API
    - Lambda integrations
    - API endpoints
    - Usage plans and rate limiting
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        document_bucket: s3.IBucket,
        metadata_table: dynamodb.ITable,
        session_table: dynamodb.ITable,
        user_pool: cognito.IUserPool,
        user_pool_client: cognito.IUserPoolClient,
        policy_store: verifiedpermissions.CfnPolicyStore,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.document_bucket = document_bucket
        self.metadata_table = metadata_table
        self.session_table = session_table
        self.user_pool = user_pool
        self.user_pool_client = user_pool_client
        self.policy_store = policy_store

        # Create Lambda functions first
        self._create_lambda_functions()
        
        # Then create API Gateway with Lambda integrations
        self._create_api_gateway()

    def _create_lambda_functions(self) -> None:
        """Create all Lambda functions"""
        self._create_authorizer_lambda()
        self._create_upload_lambda()
        self._create_upload_complete_lambda()
        self._create_download_lambda()
        self._create_list_lambda()
        self._create_delete_lambda()
        self._create_share_lambda()
        self._create_audit_lambda()
        self._create_cleanup_lambda()

    def _get_lambda_bundling_options(self, function_name: str) -> BundlingOptions:
        """Get bundling options for Python Lambda functions
        Note: /asset-input is mounted to lambda_functions/ directory
        """
        return BundlingOptions(
            image=lambda_.Runtime.PYTHON_3_11.bundling_image,
            command=[
                "bash", "-c",
                # Use /asset-output as temp directory to avoid /tmp space issues
                "export TMPDIR=/asset-output && "
                # Install dependencies from the function's requirements.txt
                f"pip install --no-cache-dir --no-warn-script-location -r /asset-input/{function_name}/requirements.txt -t /asset-output && "
                # Copy Lambda function Python files (ensure handler.py is copied)
                f"cp -au /asset-input/{function_name}/*.py /asset-output/ && "
                # Verify handler.py exists
                f"test -f /asset-output/handler.py || (echo 'ERROR: handler.py not found' && exit 1) && "
                # Copy common directory (now accessible since we mount lambda_functions/)
                "cp -au /asset-input/common /asset-output/common && "
                # Verify common module exists
                "test -d /asset-output/common || (echo 'ERROR: common directory not found' && exit 1)"
            ],
            # Set environment variables to avoid permission and space issues
            environment={
                "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_CACHE_DIR": "1",
                "TMPDIR": "/asset-output",  # Use output directory as temp to avoid /tmp space issues
            },
        )

    def _create_authorizer_lambda(self) -> None:
        """Create Lambda authorizer function"""
        lambda_functions_dir = Path(__file__).parent.parent.parent / "lambda_functions"
        lambda_dir = lambda_functions_dir / "authorizer"
        
        role = iam.Role(
            self,
            "AuthorizerLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                ),
            ],
        )
        
        self.authorizer_function = lambda_.Function(
            self,
            "AuthorizerFunction",
            function_name="document-management-authorizer",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                str(lambda_functions_dir),
                bundling=self._get_lambda_bundling_options("authorizer"),
                asset_hash_type=AssetHashType.OUTPUT,
            ),
            role=role,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                "USER_POOL_ID": self.user_pool.user_pool_id,
                "COGNITO_REGION": self.region,
                "APP_CLIENT_ID": self.user_pool_client.user_pool_client_id,
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.THREE_MONTHS,
        )

    def _create_upload_lambda(self) -> None:
        """Create upload initiation Lambda function"""
        lambda_functions_dir = Path(__file__).parent.parent.parent / "lambda_functions"
        lambda_dir = lambda_functions_dir / "upload"
        
        role = iam.Role(
            self,
            "UploadLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                ),
            ],
        )
        
        # Grant write permission for presigned URL generation (needs PutObject permission)
        # The Lambda needs this permission to generate presigned URLs for client uploads
        self.document_bucket.grant_write(role)
        self.metadata_table.grant_write_data(role)
        
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["verifiedpermissions:IsAuthorized", "verifiedpermissions:IsAuthorizedWithToken"],
                resources=[self.policy_store.attr_arn],
            )
        )
        
        self.upload_function = lambda_.Function(
            self,
            "UploadFunction",
            function_name="document-management-upload",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                str(lambda_functions_dir),
                bundling=self._get_lambda_bundling_options("upload"),
                asset_hash_type=AssetHashType.OUTPUT,
            ),
            role=role,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                "S3_BUCKET_NAME": self.document_bucket.bucket_name,
                "DYNAMODB_TABLE_NAME": self.metadata_table.table_name,
                "POLICY_STORE_ID": self.policy_store.attr_policy_store_id,
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.THREE_MONTHS,
        )

    def _create_upload_complete_lambda(self) -> None:
        """Create upload completion Lambda function"""
        lambda_functions_dir = Path(__file__).parent.parent.parent / "lambda_functions"
        lambda_dir = lambda_functions_dir / "upload_complete"
        
        role = iam.Role(
            self,
            "UploadCompleteLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                ),
            ],
        )
        
        self.document_bucket.grant_read(role)
        self.metadata_table.grant_write_data(role)
        
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": "DocumentManagement"}},
            )
        )
        
        self.upload_complete_function = lambda_.Function(
            self,
            "UploadCompleteFunction",
            function_name="document-management-upload-complete",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                str(lambda_functions_dir),
                bundling=self._get_lambda_bundling_options("upload_complete"),
                asset_hash_type=AssetHashType.OUTPUT,
            ),
            role=role,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                "DYNAMODB_TABLE_NAME": self.metadata_table.table_name,
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.THREE_MONTHS,
        )
        
        s3_upload_rule = events.Rule(
            self,
            "S3UploadCompleteRule",
            description="Trigger upload_complete Lambda when objects are created in S3",
            event_pattern=events.EventPattern(
                source=["aws.s3"],
                detail_type=["Object Created"],
                detail={
                    "bucket": {
                        "name": [self.document_bucket.bucket_name]
                    }
                }
            ),
        )
        
        s3_upload_rule.add_target(targets.LambdaFunction(self.upload_complete_function))

    def _create_download_lambda(self) -> None:
        """Create download Lambda function"""
        lambda_functions_dir = Path(__file__).parent.parent.parent / "lambda_functions"
        lambda_dir = lambda_functions_dir / "download"
        
        role = iam.Role(
            self,
            "DownloadLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                ),
            ],
        )
        
        self.document_bucket.grant_read(role)
        self.metadata_table.grant_read_data(role)
        
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["verifiedpermissions:IsAuthorized", "verifiedpermissions:IsAuthorizedWithToken"],
                resources=[self.policy_store.attr_arn],
            )
        )
        
        # Grant CloudWatch PutMetricData permission for custom metrics
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
                conditions={"StringEquals": {"cloudwatch:namespace": "DocumentManagement"}},
            )
        )
        
        self.download_function = lambda_.Function(
            self,
            "DownloadFunction",
            function_name="document-management-download",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                str(lambda_functions_dir),
                bundling=self._get_lambda_bundling_options("download"),
                asset_hash_type=AssetHashType.OUTPUT,
            ),
            role=role,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                "S3_BUCKET_NAME": self.document_bucket.bucket_name,
                "DYNAMODB_TABLE_NAME": self.metadata_table.table_name,
                "POLICY_STORE_ID": self.policy_store.attr_policy_store_id,
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.THREE_MONTHS,
        )

    def _create_list_lambda(self) -> None:
        """Create list documents Lambda function"""
        lambda_functions_dir = Path(__file__).parent.parent.parent / "lambda_functions"
        lambda_dir = lambda_functions_dir / "list"
        
        role = iam.Role(
            self,
            "ListLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                ),
            ],
        )
        
        self.metadata_table.grant_read_data(role)
        
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["verifiedpermissions:IsAuthorized", "verifiedpermissions:IsAuthorizedWithToken"],
                resources=[self.policy_store.attr_arn],
            )
        )
        
        self.list_function = lambda_.Function(
            self,
            "ListFunction",
            function_name="document-management-list",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                str(lambda_functions_dir),
                bundling=self._get_lambda_bundling_options("list"),
                asset_hash_type=AssetHashType.OUTPUT,
            ),
            role=role,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                "DYNAMODB_TABLE_NAME": self.metadata_table.table_name,
                "POLICY_STORE_ID": self.policy_store.attr_policy_store_id,
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.THREE_MONTHS,
        )

    def _create_delete_lambda(self) -> None:
        """Create delete document Lambda function"""
        lambda_functions_dir = Path(__file__).parent.parent.parent / "lambda_functions"
        lambda_dir = lambda_functions_dir / "delete"
        
        role = iam.Role(
            self,
            "DeleteLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                ),
            ],
        )
        
        self.document_bucket.grant_delete(role)
        self.metadata_table.grant_read_write_data(role)
        
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["verifiedpermissions:IsAuthorized", "verifiedpermissions:IsAuthorizedWithToken"],
                resources=[self.policy_store.attr_arn],
            )
        )
        
        self.delete_function = lambda_.Function(
            self,
            "DeleteFunction",
            function_name="document-management-delete",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                str(lambda_functions_dir),
                bundling=self._get_lambda_bundling_options("delete"),
                asset_hash_type=AssetHashType.OUTPUT,
            ),
            role=role,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                "S3_BUCKET_NAME": self.document_bucket.bucket_name,
                "DYNAMODB_TABLE_NAME": self.metadata_table.table_name,
                "POLICY_STORE_ID": self.policy_store.attr_policy_store_id,
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.THREE_MONTHS,
        )

    def _create_share_lambda(self) -> None:
        """Create share document Lambda function"""
        lambda_functions_dir = Path(__file__).parent.parent.parent / "lambda_functions"
        lambda_dir = lambda_functions_dir / "share"
        
        role = iam.Role(
            self,
            "ShareLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                ),
            ],
        )
        
        self.metadata_table.grant_read_write_data(role)
        
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "verifiedpermissions:IsAuthorized",
                    "verifiedpermissions:IsAuthorizedWithToken",
                    "verifiedpermissions:CreatePolicy",  # Required for creating share policies
                ],
                resources=[self.policy_store.attr_arn],
            )
        )
        
        self.share_function = lambda_.Function(
            self,
            "ShareFunction",
            function_name="document-management-share",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                str(lambda_functions_dir),
                bundling=self._get_lambda_bundling_options("share"),
                asset_hash_type=AssetHashType.OUTPUT,
            ),
            role=role,
            timeout=Duration.seconds(10),
            memory_size=256,
            environment={
                "DYNAMODB_TABLE_NAME": self.metadata_table.table_name,
                "POLICY_STORE_ID": self.policy_store.attr_policy_store_id,
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.THREE_MONTHS,
        )

    def _create_audit_lambda(self) -> None:
        """Create audit log retrieval Lambda function"""
        lambda_functions_dir = Path(__file__).parent.parent.parent / "lambda_functions"
        lambda_dir = lambda_functions_dir / "audit"
        
        role = iam.Role(
            self,
            "AuditLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                ),
            ],
        )
        
        self.metadata_table.grant_read_data(role)
        
        # Grant CloudWatch Logs Insights permissions
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:StartQuery",
                    "logs:GetQueryResults",
                    "logs:DescribeLogGroups",
                    "logs:DescribeLogStreams",
                ],
                resources=["*"],  # CloudWatch Logs Insights requires wildcard
            )
        )
        
        role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "verifiedpermissions:IsAuthorized",
                    "verifiedpermissions:IsAuthorizedWithToken",
                ],
                resources=[self.policy_store.attr_arn],
            )
        )
        
        self.audit_function = lambda_.Function(
            self,
            "AuditFunction",
            function_name="document-management-audit",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                str(lambda_functions_dir),
                bundling=self._get_lambda_bundling_options("audit"),
                asset_hash_type=AssetHashType.OUTPUT,
            ),
            role=role,
            timeout=Duration.seconds(30),  # Longer timeout for CloudWatch Logs Insights queries
            memory_size=512,  # More memory for query processing
            environment={
                "DYNAMODB_TABLE_NAME": self.metadata_table.table_name,
                "POLICY_STORE_ID": self.policy_store.attr_policy_store_id,
                "LOG_GROUP_PREFIX": "/aws/lambda/document-management",
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.THREE_MONTHS,
        )

    def _create_cleanup_lambda(self) -> None:
        """Create cleanup Lambda function for abandoned uploads"""
        lambda_functions_dir = Path(__file__).parent.parent.parent / "lambda_functions"
        lambda_dir = lambda_functions_dir / "cleanup"
        
        role = iam.Role(
            self,
            "CleanupLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                ),
            ],
        )
        
        self.document_bucket.grant_delete(role)
        self.metadata_table.grant_read_write_data(role)
        
        self.cleanup_function = lambda_.Function(
            self,
            "CleanupFunction",
            function_name="document-management-cleanup",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                str(lambda_functions_dir),
                bundling=self._get_lambda_bundling_options("cleanup"),
                asset_hash_type=AssetHashType.OUTPUT,
            ),
            role=role,
            timeout=Duration.seconds(60),
            memory_size=256,
            environment={
                "S3_BUCKET_NAME": self.document_bucket.bucket_name,
                "DYNAMODB_TABLE_NAME": self.metadata_table.table_name,
            },
            tracing=lambda_.Tracing.ACTIVE,
            log_retention=logs.RetentionDays.THREE_MONTHS,
        )
        
        # Schedule cleanup to run daily
        rule = events.Rule(
            self,
            "CleanupScheduleRule",
            schedule=events.Schedule.cron(hour="2", minute="0"),
            description="Daily cleanup of abandoned document uploads",
        )
        
        rule.add_target(targets.LambdaFunction(self.cleanup_function))

    def _create_api_gateway(self) -> None:
        """Create API Gateway REST API with Lambda integrations"""
        
        # Create CloudWatch Log Group for API Gateway
        api_log_group = logs.LogGroup(
            self,
            "ApiGatewayLogGroup",
            log_group_name="/aws/apigateway/document-management",
            retention=logs.RetentionDays.THREE_MONTHS,
        )
        
        # Create REST API
        self.api = apigateway.RestApi(
            self,
            "DocumentManagementApi",
            rest_api_name="document-management-api",
            description="Document Management System API",
            deploy_options=apigateway.StageOptions(
                stage_name="prod",
                throttling_rate_limit=100,
                throttling_burst_limit=200,
                logging_level=apigateway.MethodLoggingLevel.INFO,
                data_trace_enabled=True,
                access_log_destination=apigateway.LogGroupLogDestination(api_log_group),
                access_log_format=apigateway.AccessLogFormat.json_with_standard_fields(
                    caller=True,
                    http_method=True,
                    ip=True,
                    protocol=True,
                    request_time=True,
                    resource_path=True,
                    response_length=True,
                    status=True,
                    user=True,
                ),
                tracing_enabled=True,
            ),
            default_cors_preflight_options=apigateway.CorsOptions(
                allow_origins=apigateway.Cors.ALL_ORIGINS,
                allow_methods=apigateway.Cors.ALL_METHODS,
                allow_headers=["Content-Type", "Authorization", "X-Amz-Date", "X-Api-Key", "X-Amz-Security-Token"],
                max_age=Duration.hours(1),
            ),
        )
        
        # Create Lambda authorizer
        authorizer = apigateway.TokenAuthorizer(
            self,
            "ApiAuthorizer",
            handler=self.authorizer_function,
            identity_source="method.request.header.Authorization",
            results_cache_ttl=Duration.minutes(5),
        )
        
        # Create Lambda integrations
        upload_integration = apigateway.LambdaIntegration(self.upload_function)
        download_integration = apigateway.LambdaIntegration(self.download_function)
        list_integration = apigateway.LambdaIntegration(self.list_function)
        delete_integration = apigateway.LambdaIntegration(self.delete_function)
        share_integration = apigateway.LambdaIntegration(self.share_function)
        audit_integration = apigateway.LambdaIntegration(self.audit_function)
        
        # Create API resources and methods
        # RESTful design: /documents resource
        
        # /documents - GET (list), POST (upload)
        documents_resource = self.api.root.add_resource("documents")
        
        # GET /documents - List documents
        documents_resource.add_method(
            "GET",
            list_integration,
            authorizer=authorizer,
            authorization_type=apigateway.AuthorizationType.CUSTOM,
        )
        
        # POST /documents - Upload document
        documents_resource.add_method(
            "POST",
            upload_integration,
            authorizer=authorizer,
            authorization_type=apigateway.AuthorizationType.CUSTOM,
        )
        
        # /documents/{document_id} - GET (download), DELETE (delete)
        document_resource = documents_resource.add_resource("{document_id}")
        
        # GET /documents/{document_id} - Download document
        document_resource.add_method(
            "GET",
            download_integration,
            authorizer=authorizer,
            authorization_type=apigateway.AuthorizationType.CUSTOM,
        )
        
        # DELETE /documents/{document_id} - Delete document
        document_resource.add_method(
            "DELETE",
            delete_integration,
            authorizer=authorizer,
            authorization_type=apigateway.AuthorizationType.CUSTOM,
        )
        
        # /documents/{document_id}/share - POST (share document)
        share_resource = document_resource.add_resource("share")
        share_resource.add_method(
            "POST",
            share_integration,
            authorizer=authorizer,
            authorization_type=apigateway.AuthorizationType.CUSTOM,
        )
        
        # /documents/{document_id}/audit - GET (get audit logs)
        audit_resource = document_resource.add_resource("audit")
        audit_resource.add_method(
            "GET",
            audit_integration,
            authorizer=authorizer,
            authorization_type=apigateway.AuthorizationType.CUSTOM,
        )
        
        # Create usage plan
        plan = self.api.add_usage_plan(
            "DocumentManagementUsagePlan",
            name="Standard",
            throttle=apigateway.ThrottleSettings(
                rate_limit=100,
                burst_limit=200,
            ),
        )
        
        plan.add_api_stage(
            stage=self.api.deployment_stage,
        )
        
        # Outputs
        CfnOutput(
            self,
            "ApiUrl",
            value=self.api.url,
            description="API Gateway URL",
            export_name=f"{self.stack_name}-ApiUrl",
        )
        
        CfnOutput(
            self,
            "ApiId",
            value=self.api.rest_api_id,
            description="API Gateway ID",
            export_name=f"{self.stack_name}-ApiId",
        )
