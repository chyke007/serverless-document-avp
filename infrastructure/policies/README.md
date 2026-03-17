# Cedar Policies for Document Management System

This directory contains Cedar policy files for role-based access control in the AWS Document Management System.

## Policy Files

### 1. admin_full_access.cedar

Grants Admin users full access to all documents for all operations (read, write, delete, share).

**Policy Logic:**
- Principal must have role attribute
- Principal role must equal "Admin"
- Grants access to all actions on all resources

### 2. manager_departmental_access.cedar

Grants Manager users access to manage documents within their department.

**Policy Logic:**
- Principal must have role attribute equal to "Manager"
- Principal must have department attribute
- Resource must have department attribute
- Principal department must match resource department
- Grants access to read, write, delete, and share actions

### 3. editor_owned_shared_access.cedar

Grants Editor users access to read and write documents they own or have been shared with.

**Policy Logic:**
- Principal must have role attribute equal to "Editor"
- Principal must have userId attribute
- Resource must have owner attribute
- Either:
  - Resource owner matches principal userId, OR
  - Resource has sharedWith attribute containing principal userId
- Grants access to read and write actions only

### 4. viewer_read_only_access.cedar

Grants Viewer users read-only access to documents shared with them.

**Policy Logic:**
- Principal must have role attribute equal to "Viewer"
- Principal must have userId attribute
- Resource must have sharedWith attribute
- Resource sharedWith must contain principal userId
- Grants access to read action only

### 5. document_owner_permissions.cedar

Grants document owners full access to their own documents regardless of their role.

**Policy Logic:**
- Principal must have userId attribute
- Resource must have owner attribute
- Resource owner must match principal userId
- Grants access to all actions (read, write, delete, share)

## Policy Evaluation

Cedar policies are evaluated by Amazon Verified Permissions using the following process:

1. **Request Context**: Each authorization request includes:
   - Principal (User entity with userId, role, department)
   - Action (read, write, delete, or share)
   - Resource (Document entity with documentId, owner, department, sharedWith)

2. **Policy Evaluation**: All policies are evaluated against the request
   - If ANY policy explicitly permits the action, access is GRANTED
   - If NO policy permits the action, access is DENIED (default deny)

3. **Precedence**: Multiple policies can apply to a single request
   - Document owner permissions take effect regardless of role
   - Role-based policies provide additional access based on user role
   - More permissive policies (Admin) override less permissive ones

## Usage in CDK

These policies are automatically loaded and created in the Verified Permissions policy store during CDK deployment. The `DocumentManagementStack` reads each `.cedar` file and creates a corresponding `CfnPolicy` resource.

## Testing

Property-based tests validate these policies in `infrastructure/tests/`:
- Property 7: Admin users have full access to all documents
- Property 8: Manager users can manage departmental documents
- Property 9: Editor users can manage owned or shared documents
- Property 10: Viewer users have read-only access to shared documents

## Cedar Language Reference

Cedar policies use the following syntax:
- `permit()`: Grants access when conditions are met
- `principal`: The user making the request
- `action`: The operation being performed
- `resource`: The document being accessed
- `when {}`: Conditions that must be true for the policy to apply
- `has`: Checks if an attribute exists
- `==`: Equality comparison
- `contains()`: Checks if a set contains a value
- `||`: Logical OR
- `&&`: Logical AND

For more information, see the [Cedar Policy Language documentation](https://docs.cedarpolicy.com/).
