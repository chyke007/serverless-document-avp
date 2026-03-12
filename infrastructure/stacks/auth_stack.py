"""Authentication Stack - Cognito User Pool"""

from aws_cdk import (
    Stack,
    RemovalPolicy,
    aws_cognito as cognito,
    CfnOutput,
)
from constructs import Construct


class AuthStack(Stack):
    """
    Authentication infrastructure stack
    
    Creates:
    - Cognito User Pool for user authentication
    - User Pool Client for application access
    - Custom attributes for role and department
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create Cognito User Pool
        self.user_pool = cognito.UserPool(
            self,
            "DocumentManagementUserPool",
            user_pool_name="document-management-users",
            sign_in_aliases=cognito.SignInAliases(
                email=True,
                username=False,
            ),
            auto_verify=cognito.AutoVerifiedAttrs(email=True),
            password_policy=cognito.PasswordPolicy(
                min_length=8,
                require_lowercase=True,
                require_uppercase=True,
                require_digits=True,
                require_symbols=True,
            ),
            mfa=cognito.Mfa.OPTIONAL,
            mfa_second_factor=cognito.MfaSecondFactor(
                sms=False,
                otp=True,
            ),
            account_recovery=cognito.AccountRecovery.EMAIL_ONLY,
            self_sign_up_enabled=False,
            custom_attributes={
                "role": cognito.StringAttribute(
                    min_len=1,
                    max_len=50,
                    mutable=True,
                ),
                "department": cognito.StringAttribute(
                    min_len=1,
                    max_len=100,
                    mutable=True,
                ),
            },
            removal_policy=RemovalPolicy.RETAIN,
        )

        self.user_pool_client = self.user_pool.add_client(
            "StreamlitAppClient",
            user_pool_client_name="streamlit-app-client",
            auth_flows=cognito.AuthFlow(
                user_password=True,
                user_srp=True,
                custom=True,
            ),
            generate_secret=False,
            prevent_user_existence_errors=True,
        )

        # Outputs
        CfnOutput(
            self,
            "UserPoolId",
            value=self.user_pool.user_pool_id,
            description="Cognito User Pool ID",
            export_name=f"{construct_id}-UserPoolId",
        )

        CfnOutput(
            self,
            "UserPoolArn",
            value=self.user_pool.user_pool_arn,
            description="Cognito User Pool ARN",
            export_name=f"{construct_id}-UserPoolArn",
        )

        CfnOutput(
            self,
            "UserPoolClientId",
            value=self.user_pool_client.user_pool_client_id,
            description="Cognito User Pool Client ID",
            export_name=f"{construct_id}-UserPoolClientId",
        )
