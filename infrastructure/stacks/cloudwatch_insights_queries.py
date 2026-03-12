"""
CloudWatch Logs Insights Queries for Document Management System
Provides saved queries for audit trail, authorization denials, API latency, and error analysis
"""

from aws_cdk import (
    aws_logs as logs,
)
from constructs import Construct


class CloudWatchInsightsQueries:
    """
    CloudWatch Logs Insights saved queries for the Document Management System
    """
    
    @staticmethod
    def create_audit_trail_query(scope: Construct, log_group_names: list) -> logs.CfnQueryDefinition:
        """
        Create saved query for audit trail (user, action, document, result)
        
        This query retrieves all audit log entries showing:
        - User identity
        - Action performed (upload, download, delete, share, list)
        - Document identifier
        - Result (success, failure, denied)
        - Timestamp
        
        Args:
            scope: CDK construct scope
            log_group_names: List of log group names to query
            
        Returns:
            CfnQueryDefinition for audit trail query
        """
        query_string = """fields @timestamp, user_id, action, document_id, result, email, filename
| filter event_type = "audit_log"
| sort @timestamp desc
| limit 1000"""
        
        return logs.CfnQueryDefinition(
            scope,
            "AuditTrailQuery",
            name="DocumentManagement/AuditTrail",
            query_string=query_string,
            log_group_names=log_group_names,
        )
    
    @staticmethod
    def create_authorization_denials_query(scope: Construct, log_group_names: list) -> logs.CfnQueryDefinition:
        """
        Create saved query for authorization denials
        
        This query retrieves all authorization denial events showing:
        - User identity
        - Action attempted
        - Document identifier
        - Reason for denial
        - Timestamp
        - Count by action type
        
        Args:
            scope: CDK construct scope
            log_group_names: List of log group names to query
            
        Returns:
            CfnQueryDefinition for authorization denials query
        """
        query_string = """fields @timestamp, user_id, action, document_id, decision, reason
| filter event_type = "authorization_denied" or (event_type = "audit_log" and result = "denied")
| sort @timestamp desc
| limit 1000"""
        
        return logs.CfnQueryDefinition(
            scope,
            "AuthorizationDenialsQuery",
            name="DocumentManagement/AuthorizationDenials",
            query_string=query_string,
            log_group_names=log_group_names,
        )
    
    @staticmethod
    def create_authorization_denials_summary_query(scope: Construct, log_group_names: list) -> logs.CfnQueryDefinition:
        """
        Create saved query for authorization denials summary (count by action)
        
        This query provides a summary of authorization denials grouped by action type.
        
        Args:
            scope: CDK construct scope
            log_group_names: List of log group names to query
            
        Returns:
            CfnQueryDefinition for authorization denials summary query
        """
        query_string = """fields @timestamp, user_id, action, document_id
| filter event_type = "authorization_denied" or (event_type = "audit_log" and result = "denied")
| stats count() as denial_count by action
| sort denial_count desc"""
        
        return logs.CfnQueryDefinition(
            scope,
            "AuthorizationDenialsSummaryQuery",
            name="DocumentManagement/AuthorizationDenialsSummary",
            query_string=query_string,
            log_group_names=log_group_names,
        )
    
    @staticmethod
    def create_api_latency_query(scope: Construct, log_group_names: list) -> logs.CfnQueryDefinition:
        """
        Create saved query for API latency analysis
        
        This query analyzes API Gateway and Lambda execution times showing:
        - Average, max, and p99 latency by endpoint
        - Request duration
        - Timestamp
        
        Args:
            scope: CDK construct scope
            log_group_names: List of log group names to query
            
        Returns:
            CfnQueryDefinition for API latency query
        """
        query_string = """fields @timestamp, function_name, action, @duration
| filter event_type = "lambda_invocation" or event_type = "audit_log"
| stats avg(@duration) as avg_duration, max(@duration) as max_duration, pct(@duration, 99) as p99_duration by function_name, action
| sort avg_duration desc"""
        
        return logs.CfnQueryDefinition(
            scope,
            "ApiLatencyQuery",
            name="DocumentManagement/ApiLatency",
            query_string=query_string,
            log_group_names=log_group_names,
        )
    
    @staticmethod
    def create_error_analysis_query(scope: Construct, log_group_names: list) -> logs.CfnQueryDefinition:
        """
        Create saved query for error analysis
        
        This query retrieves all error events showing:
        - Error type
        - Error message
        - Function name
        - User identity (if available)
        - Timestamp
        - Count by error type
        
        Args:
            scope: CDK construct scope
            log_group_names: List of log group names to query
            
        Returns:
            CfnQueryDefinition for error analysis query
        """
        query_string = """fields @timestamp, level, event_type, function_name, user_id, message, error
| filter level = "ERROR" or event_type = "unexpected_error" or event_type like /.*_error/
| sort @timestamp desc
| limit 1000"""
        
        return logs.CfnQueryDefinition(
            scope,
            "ErrorAnalysisQuery",
            name="DocumentManagement/ErrorAnalysis",
            query_string=query_string,
            log_group_names=log_group_names,
        )
    
    @staticmethod
    def create_error_summary_query(scope: Construct, log_group_names: list) -> logs.CfnQueryDefinition:
        """
        Create saved query for error summary (count by error type)
        
        This query provides a summary of errors grouped by event type and function.
        
        Args:
            scope: CDK construct scope
            log_group_names: List of log group names to query
            
        Returns:
            CfnQueryDefinition for error summary query
        """
        query_string = """fields @timestamp, event_type, function_name, error
| filter level = "ERROR" or event_type = "unexpected_error" or event_type like /.*_error/
| stats count() as error_count by event_type, function_name
| sort error_count desc"""
        
        return logs.CfnQueryDefinition(
            scope,
            "ErrorSummaryQuery",
            name="DocumentManagement/ErrorSummary",
            query_string=query_string,
            log_group_names=log_group_names,
        )
    
    @staticmethod
    def create_authentication_events_query(scope: Construct, log_group_names: list) -> logs.CfnQueryDefinition:
        """
        Create saved query for authentication events
        
        This query retrieves all authentication events showing:
        - User identity
        - Email
        - Outcome (success, failure)
        - Role
        - Department
        - Timestamp
        
        Args:
            scope: CDK construct scope
            log_group_names: List of log group names to query
            
        Returns:
            CfnQueryDefinition for authentication events query
        """
        query_string = """fields @timestamp, user_id, email, outcome, role, department, error
| filter event_type = "authentication_success" or event_type = "authentication_failure"
| sort @timestamp desc
| limit 1000"""
        
        return logs.CfnQueryDefinition(
            scope,
            "AuthenticationEventsQuery",
            name="DocumentManagement/AuthenticationEvents",
            query_string=query_string,
            log_group_names=log_group_names,
        )
    
    @staticmethod
    def create_user_activity_query(scope: Construct, log_group_names: list) -> logs.CfnQueryDefinition:
        """
        Create saved query for user activity (actions by user)
        
        This query provides a summary of user activity showing action counts per user.
        
        Args:
            scope: CDK construct scope
            log_group_names: List of log group names to query
            
        Returns:
            CfnQueryDefinition for user activity query
        """
        query_string = """fields @timestamp, user_id, email, action, result
| filter event_type = "audit_log"
| stats count() as action_count by user_id, email, action, result
| sort action_count desc"""
        
        return logs.CfnQueryDefinition(
            scope,
            "UserActivityQuery",
            name="DocumentManagement/UserActivity",
            query_string=query_string,
            log_group_names=log_group_names,
        )
    
    @staticmethod
    def create_document_access_query(scope: Construct, log_group_names: list) -> logs.CfnQueryDefinition:
        """
        Create saved query for document access patterns
        
        This query shows which documents are being accessed most frequently.
        
        Args:
            scope: CDK construct scope
            log_group_names: List of log group names to query
            
        Returns:
            CfnQueryDefinition for document access query
        """
        query_string = """fields @timestamp, document_id, filename, action, user_id, result
| filter event_type = "audit_log" and document_id != "N/A"
| stats count() as access_count by document_id, filename, action
| sort access_count desc
| limit 100"""
        
        return logs.CfnQueryDefinition(
            scope,
            "DocumentAccessQuery",
            name="DocumentManagement/DocumentAccess",
            query_string=query_string,
            log_group_names=log_group_names,
        )
    
    @staticmethod
    def create_all_queries(scope: Construct, lambda_functions: dict) -> list:
        """
        Create all CloudWatch Logs Insights saved queries
        
        Args:
            scope: CDK construct scope
            lambda_functions: Dictionary of Lambda function names to function objects
            
        Returns:
            List of created query definitions
        """
        # Build list of log group names from Lambda functions
        log_group_names = []
        for func in lambda_functions.values():
            if hasattr(func, 'log_group') and func.log_group:
                log_group_names.append(func.log_group.log_group_name)
        
        # Create all queries
        queries = [
            CloudWatchInsightsQueries.create_audit_trail_query(scope, log_group_names),
            CloudWatchInsightsQueries.create_authorization_denials_query(scope, log_group_names),
            CloudWatchInsightsQueries.create_authorization_denials_summary_query(scope, log_group_names),
            CloudWatchInsightsQueries.create_api_latency_query(scope, log_group_names),
            CloudWatchInsightsQueries.create_error_analysis_query(scope, log_group_names),
            CloudWatchInsightsQueries.create_error_summary_query(scope, log_group_names),
            CloudWatchInsightsQueries.create_authentication_events_query(scope, log_group_names),
            CloudWatchInsightsQueries.create_user_activity_query(scope, log_group_names),
            CloudWatchInsightsQueries.create_document_access_query(scope, log_group_names),
        ]
        
        return queries
