"""
Lambda Authorizer for API Gateway
Validates Cognito JWT tokens and generates IAM policies
"""

import json
import os
import sys
import time
from typing import Dict, Any, Optional
import urllib.request
import base64
from jose import jwk, jwt
from jose.utils import base64url_decode

# Add common module to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'common'))
from common.structured_logger import create_logger, EventType

# AWS X-Ray tracing
from aws_xray_sdk.core import xray_recorder
from aws_xray_sdk.core import patch_all

# Patch AWS SDK for X-Ray tracing
patch_all()

# Environment variables
USER_POOL_ID = os.environ.get('USER_POOL_ID')
REGION = os.environ.get('COGNITO_REGION', os.environ.get('AWS_REGION', 'us-east-1'))
APP_CLIENT_ID = os.environ.get('APP_CLIENT_ID')

# Cognito JWKS URL
JWKS_URL = f'https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}/.well-known/jwks.json'

# Cache for JWKS keys (loaded once per Lambda container lifecycle)
_jwks_cache: Optional[Dict[str, Any]] = None


def get_jwks() -> Dict[str, Any]:
    """
    Fetch and cache Cognito public keys for JWT validation
    
    Returns:
        Dictionary containing JWKS keys
    """
    global _jwks_cache
    
    if _jwks_cache is None:
        with urllib.request.urlopen(JWKS_URL) as response:
            _jwks_cache = json.loads(response.read())
    
    return _jwks_cache


@xray_recorder.capture('validate_token')
def validate_token(token: str) -> Dict[str, Any]:
    """
    Validates Cognito JWT token
    
    Args:
        token: JWT token string
        
    Returns:
        Token claims if valid
        
    Raises:
        Exception: If token is invalid, expired, or from wrong issuer
    """
    # Get JWKS keys
    jwks = get_jwks()
    
    # Get the kid from the token header
    headers = jwt.get_unverified_headers(token)
    kid = headers.get('kid')
    
    if not kid:
        raise Exception('Token missing kid in header')
    
    # Find the key with matching kid
    key = None
    for jwk_key in jwks.get('keys', []):
        if jwk_key.get('kid') == kid:
            key = jwk_key
            break
    
    if not key:
        raise Exception(f'Public key not found for kid: {kid}')
    
    # Construct the public key
    public_key = jwk.construct(key)
    
    # Get the message and signature from token
    message, encoded_signature = token.rsplit('.', 1)
    decoded_signature = base64url_decode(encoded_signature.encode('utf-8'))
    
    # Verify the signature
    if not public_key.verify(message.encode('utf-8'), decoded_signature):
        raise Exception('Signature verification failed')
    
    # Decode and validate the token
    claims = jwt.get_unverified_claims(token)
    
    # Verify token expiration
    current_time = time.time()
    if current_time > claims.get('exp', 0):
        raise Exception('Token has expired')
    
    # Verify issuer
    expected_issuer = f'https://cognito-idp.{REGION}.amazonaws.com/{USER_POOL_ID}'
    if claims.get('iss') != expected_issuer:
        raise Exception(f'Invalid issuer: {claims.get("iss")}')
    
    # Verify token use (should be 'access' or 'id')
    token_use = claims.get('token_use')
    if token_use not in ['access', 'id']:
        raise Exception(f'Invalid token_use: {token_use}')
    
    # Verify client ID for id tokens
    if token_use == 'id':
        aud = claims.get('aud')
        if aud != APP_CLIENT_ID:
            raise Exception(f'Invalid audience: {aud}')
    
    # Verify client ID for access tokens
    if token_use == 'access':
        client_id = claims.get('client_id')
        if client_id != APP_CLIENT_ID:
            raise Exception(f'Invalid client_id: {client_id}')
    
    return claims


def generate_policy(principal_id: str, effect: str, resource: str, context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generates IAM policy document for API Gateway
    
    Args:
        principal_id: User identifier
        effect: 'Allow' or 'Deny'
        resource: API Gateway method ARN
        context: Additional context to pass to Lambda functions
        
    Returns:
        IAM policy document
    """
    policy = {
        'principalId': principal_id,
        'policyDocument': {
            'Version': '2012-10-17',
            'Statement': [
                {
                    'Action': 'execute-api:Invoke',
                    'Effect': effect,
                    'Resource': resource
                }
            ]
        },
        'context': context
    }
    
    return policy


@xray_recorder.capture('lambda_handler')
def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Lambda authorizer handler for API Gateway
    
    Validates Cognito JWT token and returns IAM policy
    
    Args:
        event: API Gateway authorizer event containing methodArn and authorizationToken
        context: Lambda context
        
    Returns:
        IAM policy document with principalId and context
    """
    # Create structured logger
    logger = create_logger('lambda-authorizer', context)
    
    # Log the incoming event (without sensitive token data)
    logger.info(
        EventType.LAMBDA_INVOCATION,
        "Authorizer Lambda invoked",
        method_arn=event.get('methodArn')
    )
    
    try:
        # Extract token from Authorization header
        # Format: "Bearer <token>"
        authorization_token = event.get('authorizationToken', '')
        
        if not authorization_token:
            raise Exception('Missing Authorization header')
        
        # Remove 'Bearer ' prefix if present
        if authorization_token.startswith('Bearer '):
            token = authorization_token[7:]
        else:
            token = authorization_token
        
        # Validate the token
        claims = validate_token(token)
        
        # Extract user identity and attributes
        user_id = claims.get('sub')  # Cognito user ID (UUID)
        email = claims.get('email', '')
        
        # Extract custom attributes (role and department)
        # Custom attributes in Cognito are prefixed with 'custom:'
        role = claims.get('custom:role', 'Viewer')  # Default to Viewer if not set
        department = claims.get('custom:department', '')
        
        # Get username (email in our case)
        username = claims.get('cognito:username', email)
        
        # Set user_id in logger
        logger.set_user_id(user_id)
        
        # Log successful authentication
        logger.authentication_event(
            outcome='success',
            email=email,
            role=role,
            department=department
        )
        
        # Add X-Ray annotation for user
        xray_recorder.put_annotation('user_id', user_id)
        xray_recorder.put_annotation('role', role)
        
        # Generate Allow policy with user context
        # This context will be available to Lambda functions via event.requestContext.authorizer
        method_arn = event.get('methodArn', '')
        
        # Allow access to all methods in the API (wildcard)
        # API Gateway will cache this policy for the user
        arn_parts = method_arn.split(':')
        api_gateway_arn_parts = arn_parts[5].split('/')
        aws_account_id = arn_parts[4]
        region = arn_parts[3]
        rest_api_id = api_gateway_arn_parts[0]
        stage = api_gateway_arn_parts[1]
        
        # Construct wildcard resource ARN for all methods
        resource = f'arn:aws:execute-api:{region}:{aws_account_id}:{rest_api_id}/{stage}/*/*'
        
        user_context = {
            'userId': user_id,
            'email': email,
            'role': role,
            'department': department,
            'username': username
        }
        
        return generate_policy(user_id, 'Allow', resource, user_context)
        
    except Exception as e:
        # Log authentication failure
        error_message = str(e)
        logger.authentication_event(
            outcome='failure',
            error=error_message
        )
        
        # Add X-Ray annotation for failure
        xray_recorder.put_annotation('authorization_result', 'denied')
        
        # Return Deny policy
        # Note: API Gateway requires a policy to be returned, not an exception
        # Returning 'Deny' will result in 403 Forbidden
        raise Exception('Unauthorized')  # This will cause API Gateway to return 401
