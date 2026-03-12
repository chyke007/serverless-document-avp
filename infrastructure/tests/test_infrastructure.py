"""Basic infrastructure validation tests"""

import aws_cdk as cdk
from aws_cdk.assertions import Template
from stacks.document_management_stack import DocumentManagementStack


def test_vpc_created():
    """Test that VPC is created with correct configuration"""
    app = cdk.App()
    stack = DocumentManagementStack(app, "TestStack")
    template = Template.from_stack(stack)
    
    # Verify VPC is created
    template.resource_count_is("AWS::EC2::VPC", 1)
    
    # Verify VPC has correct CIDR block
    template.has_resource_properties("AWS::EC2::VPC", {
        "CidrBlock": "10.0.0.0/16",
        "EnableDnsHostnames": True,
        "EnableDnsSupport": True,
    })


def test_subnets_created():
    """Test that public and private subnets are created"""
    app = cdk.App()
    stack = DocumentManagementStack(app, "TestStack")
    template = Template.from_stack(stack)
    
    # Verify 2 public subnets (one per AZ)
    template.resource_count_is("AWS::EC2::Subnet", 4)  # 2 public + 2 private
    
    # Verify NAT Gateway is created
    template.resource_count_is("AWS::EC2::NatGateway", 1)
    
    # Verify Internet Gateway is created
    template.resource_count_is("AWS::EC2::InternetGateway", 1)


def test_stack_outputs():
    """Test that stack outputs are defined"""
    app = cdk.App()
    stack = DocumentManagementStack(app, "TestStack")
    template = Template.from_stack(stack)
    
    # Verify outputs exist
    outputs = template.find_outputs("*")
    assert "VpcId" in outputs
    assert "VpcCidr" in outputs



def test_download_lambda_created():
    """Test that download Lambda function is created with correct configuration"""
    app = cdk.App()
    stack = DocumentManagementStack(app, "TestStack")
    template = Template.from_stack(stack)
    
    # Verify Lambda function is created
    template.has_resource_properties("AWS::Lambda::Function", {
        "FunctionName": "document-management-download",
        "Runtime": "python3.11",
        "Handler": "handler.lambda_handler",
        "Timeout": 10,
        "MemorySize": 256,
    })
    
    # Verify Lambda has X-Ray tracing enabled
    template.has_resource_properties("AWS::Lambda::Function", {
        "FunctionName": "document-management-download",
        "TracingConfig": {
            "Mode": "Active"
        }
    })


def test_download_lambda_has_required_permissions():
    """Test that download Lambda has required IAM permissions"""
    app = cdk.App()
    stack = DocumentManagementStack(app, "TestStack")
    template = Template.from_stack(stack)
    
    # Verify IAM role is created for download Lambda
    template.has_resource_properties("AWS::IAM::Role", {
        "AssumeRolePolicyDocument": {
            "Statement": [{
                "Action": "sts:AssumeRole",
                "Effect": "Allow",
                "Principal": {
                    "Service": "lambda.amazonaws.com"
                }
            }]
        },
        "ManagedPolicyArns": [
            {
                "Fn::Join": [
                    "",
                    [
                        "arn:",
                        {"Ref": "AWS::Partition"},
                        ":iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
                    ]
                ]
            },
            {
                "Fn::Join": [
                    "",
                    [
                        "arn:",
                        {"Ref": "AWS::Partition"},
                        ":iam::aws:policy/AWSXRayDaemonWriteAccess"
                    ]
                ]
            }
        ]
    })


def test_download_lambda_outputs():
    """Test that download Lambda outputs are defined"""
    app = cdk.App()
    stack = DocumentManagementStack(app, "TestStack")
    template = Template.from_stack(stack)
    
    # Verify outputs exist
    outputs = template.find_outputs("*")
    assert "DownloadFunctionName" in outputs
    assert "DownloadFunctionArn" in outputs
