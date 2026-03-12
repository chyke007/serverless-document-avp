"""Observability Stack - CloudWatch dashboards, alarms, and monitoring"""

from aws_cdk import (
    Stack,
    Duration,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cloudwatch_actions,
    aws_sns as sns,
    aws_sns_subscriptions as sns_subscriptions,
    aws_lambda as lambda_,
    aws_apigateway as apigateway,
    aws_dynamodb as dynamodb,
    aws_logs as logs,
    CfnOutput,
)
from constructs import Construct
from .cloudwatch_insights_queries import CloudWatchInsightsQueries


class ObservabilityStack(Stack):
    """
    Observability infrastructure stack
    
    Creates:
    - CloudWatch Dashboard
    - CloudWatch Alarms
    - SNS topic for alarm notifications
    - CloudWatch Logs Insights queries
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        authorizer_function: lambda_.IFunction,
        upload_function: lambda_.IFunction,
        download_function: lambda_.IFunction,
        list_function: lambda_.IFunction,
        delete_function: lambda_.IFunction,
        share_function: lambda_.IFunction,
        cleanup_function: lambda_.IFunction,
        api: apigateway.IRestApi,
        metadata_table: dynamodb.ITable,
        **kwargs
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.lambda_functions = {
            "authorizer": authorizer_function,
            "upload": upload_function,
            "download": download_function,
            "list": list_function,
            "delete": delete_function,
            "share": share_function,
            "cleanup": cleanup_function,
        }
        self.api = api
        self.metadata_table = metadata_table

        # Create SNS topic for alarms
        self._create_sns_topic()
        
        # Create CloudWatch alarms
        self._create_cloudwatch_alarms()
        
        # Create CloudWatch dashboard
        self._create_cloudwatch_dashboard()
        
        # Create CloudWatch Logs Insights queries
        self._create_logs_insights_queries()

    def _create_sns_topic(self) -> None:
        """Create SNS topic for alarm notifications"""
        self.alarm_topic = sns.Topic(
            self,
            "AlarmTopic",
            topic_name="document-management-alarms",
            display_name="Document Management System Alarms",
        )
        
        CfnOutput(
            self,
            "AlarmTopicArn",
            value=self.alarm_topic.topic_arn,
            description="SNS topic ARN for CloudWatch alarms",
            export_name=f"{self.stack_name}-AlarmTopicArn",
        )

    def _create_cloudwatch_alarms(self) -> None:
        """Create CloudWatch alarms for monitoring"""
        
        # Lambda error alarms
        for name, function in self.lambda_functions.items():
            alarm = cloudwatch.Alarm(
                self,
                f"{name.title()}LambdaErrorAlarm",
                alarm_name=f"document-management-{name}-errors",
                metric=function.metric_errors(
                    statistic="Sum",
                    period=Duration.minutes(5),
                ),
                threshold=5,
                evaluation_periods=1,
                comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            )
            alarm.add_alarm_action(cloudwatch_actions.SnsAction(self.alarm_topic))
        
        # API Gateway 5xx errors
        api_error_alarm = cloudwatch.Alarm(
            self,
            "ApiGateway5xxAlarm",
            alarm_name="document-management-api-5xx-errors",
            metric=cloudwatch.Metric(
                namespace="AWS/ApiGateway",
                metric_name="5XXError",
                dimensions_map={"ApiName": "document-management-api"},
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=10,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        api_error_alarm.add_alarm_action(cloudwatch_actions.SnsAction(self.alarm_topic))
        
        # API Gateway latency
        api_latency_alarm = cloudwatch.Alarm(
            self,
            "ApiGatewayLatencyAlarm",
            alarm_name="document-management-api-latency",
            metric=cloudwatch.Metric(
                namespace="AWS/ApiGateway",
                metric_name="Latency",
                dimensions_map={"ApiName": "document-management-api"},
                statistic="Average",
                period=Duration.minutes(5),
            ),
            threshold=1000,  # 1 second
            evaluation_periods=2,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        api_latency_alarm.add_alarm_action(cloudwatch_actions.SnsAction(self.alarm_topic))
        
        # DynamoDB throttling
        dynamodb_throttle_alarm = cloudwatch.Alarm(
            self,
            "DynamoDbThrottleAlarm",
            alarm_name="document-management-dynamodb-throttle",
            metric=self.metadata_table.metric_user_errors(
                statistic="Sum",
                period=Duration.minutes(5),
            ),
            threshold=10,
            evaluation_periods=1,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
        )
        dynamodb_throttle_alarm.add_alarm_action(cloudwatch_actions.SnsAction(self.alarm_topic))

    def _create_cloudwatch_dashboard(self) -> None:
        """Create CloudWatch dashboard for system monitoring"""
        
        dashboard = cloudwatch.Dashboard(
            self,
            "DocumentManagementDashboard",
            dashboard_name="DocumentManagementSystem",
        )
        
        # Lambda metrics
        lambda_widgets = []
        for name, function in self.lambda_functions.items():
            lambda_widgets.append(
                cloudwatch.GraphWidget(
                    title=f"{name.title()} Lambda",
                    left=[
                        function.metric_invocations(statistic="Sum"),
                        function.metric_errors(statistic="Sum"),
                    ],
                    right=[
                        function.metric_duration(statistic="Average"),
                    ],
                )
            )
        
        # API Gateway metrics
        api_widget = cloudwatch.GraphWidget(
            title="API Gateway",
            left=[
                cloudwatch.Metric(
                    namespace="AWS/ApiGateway",
                    metric_name="Count",
                    dimensions_map={"ApiName": "document-management-api"},
                    statistic="Sum",
                ),
                cloudwatch.Metric(
                    namespace="AWS/ApiGateway",
                    metric_name="4XXError",
                    dimensions_map={"ApiName": "document-management-api"},
                    statistic="Sum",
                ),
                cloudwatch.Metric(
                    namespace="AWS/ApiGateway",
                    metric_name="5XXError",
                    dimensions_map={"ApiName": "document-management-api"},
                    statistic="Sum",
                ),
            ],
            right=[
                cloudwatch.Metric(
                    namespace="AWS/ApiGateway",
                    metric_name="Latency",
                    dimensions_map={"ApiName": "document-management-api"},
                    statistic="Average",
                ),
            ],
        )
        
        # DynamoDB metrics
        dynamodb_widget = cloudwatch.GraphWidget(
            title="DynamoDB",
            left=[
                self.metadata_table.metric_consumed_read_capacity_units(statistic="Sum"),
                self.metadata_table.metric_consumed_write_capacity_units(statistic="Sum"),
            ],
            right=[
                self.metadata_table.metric_user_errors(statistic="Sum"),
                self.metadata_table.metric_system_errors_for_operations(statistic="Sum"),
            ],
        )
        
        # Add widgets to dashboard
        dashboard.add_widgets(api_widget)
        dashboard.add_widgets(dynamodb_widget)
        for widget in lambda_widgets:
            dashboard.add_widgets(widget)
        
        CfnOutput(
            self,
            "DashboardUrl",
            value=f"https://console.aws.amazon.com/cloudwatch/home?region={self.region}#dashboards:name=DocumentManagementSystem",
            description="CloudWatch Dashboard URL",
        )

    def _create_logs_insights_queries(self) -> None:
        """Create CloudWatch Logs Insights saved queries"""
        
        # Create all saved queries for audit trail, errors, latency, etc.
        CloudWatchInsightsQueries.create_all_queries(
            self,
            self.lambda_functions,
        )
