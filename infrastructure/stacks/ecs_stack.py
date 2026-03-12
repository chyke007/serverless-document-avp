"""ECS Stack for Streamlit Application"""

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_elasticloadbalancingv2 as elbv2,
    aws_iam as iam,
    aws_logs as logs,
    aws_ecr as ecr,
    aws_dynamodb as dynamodb,
    aws_cognito as cognito,
    aws_apigateway as apigateway,
    CfnOutput,
)
from constructs import Construct


class EcsStack(Stack):
    """
    ECS Stack for Streamlit Application
    
    Creates:
    - ECS Cluster
    - Application Load Balancer
    - Fargate Service with auto-scaling
    - Security groups and networking
    
    This stack should be deployed AFTER the main infrastructure stack
    and AFTER building and pushing the Docker image to ECR.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        ecr_repository: ecr.IRepository,
        user_pool: cognito.IUserPool,
        user_pool_client: cognito.IUserPoolClient,
        api: apigateway.IRestApi,
        session_table: dynamodb.ITable,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = vpc
        self.ecr_repository = ecr_repository
        self.user_pool = user_pool
        self.user_pool_client = user_pool_client
        self.api = api
        self.session_table = session_table

        # Create ECS resources
        self._create_ecs_cluster()
        self._create_alb()
        self._create_ecs_service()

    def _create_ecs_cluster(self) -> None:
        """Create ECS cluster for running Fargate tasks"""
        self.ecs_cluster = ecs.Cluster(
            self,
            "StreamlitEcsCluster",
            cluster_name="document-management-cluster",
            vpc=self.vpc,
            container_insights=True,
        )

        CfnOutput(
            self,
            "EcsClusterName",
            value=self.ecs_cluster.cluster_name,
            description="ECS cluster name for Streamlit application",
        )

    def _create_alb(self) -> None:
        """Create Application Load Balancer for Streamlit application"""
        # Create security group for ALB
        self.alb_security_group = ec2.SecurityGroup(
            self,
            "AlbSecurityGroup",
            vpc=self.vpc,
            description="Security group for Application Load Balancer",
            allow_all_outbound=True,
        )

        # Allow inbound HTTP traffic
        self.alb_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(80),
            description="Allow HTTP traffic from anywhere",
        )

        # Allow inbound HTTPS traffic
        self.alb_security_group.add_ingress_rule(
            peer=ec2.Peer.any_ipv4(),
            connection=ec2.Port.tcp(443),
            description="Allow HTTPS traffic from anywhere",
        )

        # Create Application Load Balancer
        self.alb = elbv2.ApplicationLoadBalancer(
            self,
            "StreamlitAlb",
            vpc=self.vpc,
            internet_facing=True,
            load_balancer_name="document-management-alb",
            security_group=self.alb_security_group,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PUBLIC,
            ),
            deletion_protection=False,
        )

        # Create target group for ECS service
        self.target_group = elbv2.ApplicationTargetGroup(
            self,
            "StreamlitTargetGroup",
            vpc=self.vpc,
            port=8501,
            protocol=elbv2.ApplicationProtocol.HTTP,
            target_type=elbv2.TargetType.IP,
            target_group_name="streamlit-targets",
            health_check=elbv2.HealthCheck(
                enabled=True,
                path="/_stcore/health",
                protocol=elbv2.Protocol.HTTP,
                port="8501",
                healthy_threshold_count=2,
                unhealthy_threshold_count=3,
                timeout=Duration.seconds(5),
                interval=Duration.seconds(30),
            ),
            deregistration_delay=Duration.seconds(30),
        )

        # Add HTTP listener
        self.http_listener = self.alb.add_listener(
            "HttpListener",
            port=80,
            protocol=elbv2.ApplicationProtocol.HTTP,
            default_target_groups=[self.target_group],
        )

        # Output ALB details
        CfnOutput(
            self,
            "AlbDnsName",
            value=self.alb.load_balancer_dns_name,
            description="Application Load Balancer DNS name",
        )

        CfnOutput(
            self,
            "AlbUrl",
            value=f"http://{self.alb.load_balancer_dns_name}",
            description="Streamlit application URL",
        )

    def _create_ecs_service(self) -> None:
        """Create ECS Fargate service for Streamlit application"""
        # Create CloudWatch Log Group
        ecs_log_group = logs.LogGroup(
            self,
            "EcsLogGroup",
            log_group_name="/ecs/streamlit-app",
            retention=logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.RETAIN,
        )

        # Create task execution role
        task_execution_role = iam.Role(
            self,
            "EcsTaskExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Execution role for ECS Fargate tasks",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                ),
            ],
        )

        # Grant ECR pull permissions
        self.ecr_repository.grant_pull(task_execution_role)

        # Create task role
        task_role = iam.Role(
            self,
            "EcsTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            description="Task role for Streamlit application container",
        )

        # Grant Cognito permissions for authentication
        task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "cognito-idp:InitiateAuth",
                    "cognito-idp:RespondToAuthChallenge",
                    "cognito-idp:GetUser",
                    "cognito-idp:SignUp",
                    "cognito-idp:ConfirmSignUp",
                    "cognito-idp:ForgotPassword",
                    "cognito-idp:ConfirmForgotPassword",
                ],
                resources=[self.user_pool.user_pool_arn],
            )
        )
        
        # Grant Cognito admin permissions for user management
        task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "cognito-idp:ListUsers",
                    "cognito-idp:AdminGetUser",
                    "cognito-idp:AdminCreateUser",
                    "cognito-idp:AdminDeleteUser",
                    "cognito-idp:AdminUpdateUserAttributes",
                    "cognito-idp:AdminSetUserPassword",
                    "cognito-idp:AdminEnableUser",
                    "cognito-idp:AdminDisableUser",
                ],
                resources=[self.user_pool.user_pool_arn],
            )
        )

        # Grant session table permissions
        self.session_table.grant_read_write_data(task_role)

        # Create Fargate task definition
        task_definition = ecs.FargateTaskDefinition(
            self,
            "StreamlitTaskDefinition",
            family="streamlit-app",
            cpu=512,
            memory_limit_mib=1024,
            task_role=task_role,
            execution_role=task_execution_role,
        )

        # Add container to task definition
        container = task_definition.add_container(
            "StreamlitContainer",
            image=ecs.ContainerImage.from_ecr_repository(
                repository=self.ecr_repository,
                tag="latest",
            ),
            logging=ecs.LogDriver.aws_logs(
                stream_prefix="streamlit",
                log_group=ecs_log_group,
            ),
            environment={
                "API_GATEWAY_URL": self.api.url,
                "COGNITO_USER_POOL_ID": self.user_pool.user_pool_id,
                "COGNITO_CLIENT_ID": self.user_pool_client.user_pool_client_id,
                "COGNITO_REGION": self.region,
                "SESSION_TABLE_NAME": self.session_table.table_name,
                "AWS_REGION": self.region,
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:8501/_stcore/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(60),
            ),
        )

        # Add port mapping
        container.add_port_mappings(
            ecs.PortMapping(
                container_port=8501,
                protocol=ecs.Protocol.TCP,
            )
        )

        # Create security group for ECS tasks
        self.ecs_security_group = ec2.SecurityGroup(
            self,
            "EcsSecurityGroup",
            vpc=self.vpc,
            description="Security group for ECS Fargate tasks",
            allow_all_outbound=True,
        )

        # Allow inbound traffic from ALB
        self.ecs_security_group.add_ingress_rule(
            peer=self.alb_security_group,
            connection=ec2.Port.tcp(8501),
            description="Allow traffic from ALB to Streamlit container",
        )

        # Create ECS service
        self.ecs_service = ecs.FargateService(
            self,
            "StreamlitService",
            cluster=self.ecs_cluster,
            task_definition=task_definition,
            service_name="streamlit-service",
            desired_count=2,
            vpc_subnets=ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
            ),
            security_groups=[self.ecs_security_group],
            assign_public_ip=False,
            health_check_grace_period=Duration.seconds(60),
            enable_execute_command=True,
        )

        # Attach to ALB target group
        self.ecs_service.attach_to_application_target_group(self.target_group)

        # Configure auto-scaling
        scaling = self.ecs_service.auto_scale_task_count(
            min_capacity=2,
            max_capacity=10,
        )

        # Scale on CPU
        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
            scale_in_cooldown=Duration.seconds(60),
            scale_out_cooldown=Duration.seconds(60),
        )

        # Scale on memory
        scaling.scale_on_memory_utilization(
            "MemoryScaling",
            target_utilization_percent=80,
            scale_in_cooldown=Duration.seconds(60),
            scale_out_cooldown=Duration.seconds(60),
        )

        # Output ECS service details
        CfnOutput(
            self,
            "EcsServiceName",
            value=self.ecs_service.service_name,
            description="ECS service name",
        )

        CfnOutput(
            self,
            "StreamlitAppInstructions",
            value=f"1. Build: cd streamlit_app && docker build -t streamlit-app . "
                  f"2. Tag: docker tag streamlit-app:latest {self.ecr_repository.repository_uri}:latest "
                  f"3. Login: aws ecr get-login-password --region {self.region} | docker login --username AWS --password-stdin {self.ecr_repository.repository_uri} "
                  f"4. Push: docker push {self.ecr_repository.repository_uri}:latest "
                  f"5. Update service: aws ecs update-service --cluster {self.ecs_cluster.cluster_name} --service {self.ecs_service.service_name} --force-new-deployment",
            description="Instructions for deploying Streamlit Docker image",
        )
