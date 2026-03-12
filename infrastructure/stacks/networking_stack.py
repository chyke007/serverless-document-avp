"""Networking Stack - VPC and related resources"""

from aws_cdk import (
    Stack,
    aws_ec2 as ec2,
    CfnOutput,
)
from constructs import Construct


class NetworkingStack(Stack):
    """
    Networking infrastructure stack
    
    Creates:
    - VPC with public and private subnets across 2 AZs
    - NAT Gateway for private subnet internet access
    - Internet Gateway
    - Route tables
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Create VPC with public and private subnets across 2 AZs
        self.vpc = ec2.Vpc(
            self,
            "DocumentManagementVPC",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )

        # Outputs
        CfnOutput(
            self,
            "VpcId",
            value=self.vpc.vpc_id,
            description="VPC ID for Document Management System",
            export_name=f"{construct_id}-VpcId",
        )

        CfnOutput(
            self,
            "VpcCidr",
            value=self.vpc.vpc_cidr_block,
            description="VPC CIDR block",
            export_name=f"{construct_id}-VpcCidr",
        )
