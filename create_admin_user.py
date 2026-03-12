
import boto3
import sys
import argparse


def get_user_pool_id():
    try:
        cfn = boto3.client('cloudformation')
        
        # Try to find the stack
        stacks = cfn.list_stacks(StackStatusFilter=['CREATE_COMPLETE', 'UPDATE_COMPLETE'])
        
        for stack_summary in stacks['StackSummaries']:
            stack_name = stack_summary['StackName']
            if 'DocumentManagement' in stack_name and 'Auth' in stack_name:
                # Get stack outputs
                stack = cfn.describe_stacks(StackName=stack_name)
                outputs = stack['Stacks'][0].get('Outputs', [])
                
                for output in outputs:
                    if 'UserPoolId' in output['OutputKey']:
                        return output['OutputValue']
        
        print("❌ Could not find User Pool ID in CloudFormation outputs")
        print("   Please provide it manually using --user-pool-id parameter")
        return None
        
    except Exception as e:
        print(f"❌ Error getting User Pool ID: {e}")
        return None


def create_admin_user(user_pool_id, email, password, department='IT'):
    client = boto3.client('cognito-idp')
    
    try:
        print(f"\n🔧 Creating admin user in User Pool: {user_pool_id}")
        print(f"   Email: {email}")
        print(f"   Department: {department}")
        
        # Create user
        response = client.admin_create_user(
            UserPoolId=user_pool_id,
            Username=email,
            UserAttributes=[
                {'Name': 'email', 'Value': email},
                {'Name': 'email_verified', 'Value': 'true'},
                {'Name': 'custom:role', 'Value': 'Admin'},
                {'Name': 'custom:department', 'Value': department}
            ],
            TemporaryPassword=password,
            MessageAction='SUPPRESS'  # Don't send email
        )
        
        print(f"✅ User created successfully")
        
        # Set permanent password
        client.admin_set_user_password(
            UserPoolId=user_pool_id,
            Username=email,
            Password=password,
            Permanent=True
        )
        
        print(f"✅ Password set to permanent")
        
        # Print summary
        print(f"\n{'='*60}")
        print(f"✅ ADMIN USER CREATED SUCCESSFULLY")
        print(f"{'='*60}")
        print(f"User Pool ID: {user_pool_id}")
        print(f"Username:     {email}")
        print(f"Password:     {password}")
        print(f"Role:         Admin")
        print(f"Department:   {department}")
        print(f"{'='*60}")
        print(f"\n📝 IMPORTANT:")
        print(f"   1. Save these credentials securely")
        print(f"   2. Change the password after first login")
        print(f"   3. Enable MFA for additional security")
        print(f"\n🌐 You can now log in to the Streamlit application")
        print(f"   with these credentials.\n")
        
        return True
        
    except client.exceptions.UsernameExistsException:
        print(f"\n❌ Error: User {email} already exists")
        print(f"   Use a different email or delete the existing user first")
        return False
        
    except Exception as e:
        print(f"\n❌ Error creating admin user: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Create an admin user for AWS Document Management System',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Auto-detect User Pool ID
  python create_admin_user.py admin@example.com SecurePassword123!
  
  # Specify User Pool ID manually
  python create_admin_user.py admin@example.com SecurePassword123! --user-pool-id us-east-1_XXXXXXXXX
  
  # Specify department
  python create_admin_user.py admin@example.com SecurePassword123! --department Engineering

Password Requirements:
  - Minimum 8 characters
  - At least one uppercase letter
  - At least one lowercase letter
  - At least one number
  - At least one special character (!@#$%^&*)
        """
    )
    
    parser.add_argument('email', help='Admin user email address')
    parser.add_argument('password', help='Admin user password')
    parser.add_argument('--user-pool-id', help='Cognito User Pool ID (auto-detected if not provided)')
    parser.add_argument('--department', default='IT', help='Department name (default: IT)')
    
    args = parser.parse_args()
    
    user_pool_id = args.user_pool_id
    if not user_pool_id:
        print("🔍 Auto-detecting User Pool ID from CloudFormation...")
        user_pool_id = get_user_pool_id()
        if not user_pool_id:
            sys.exit(1)
    
    if len(args.password) < 8:
        print("❌ Error: Password must be at least 8 characters")
        sys.exit(1)
    
    success = create_admin_user(user_pool_id, args.email, args.password, args.department)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
