"""Authorization Stack - Verified Permissions and Cedar Policies"""

from aws_cdk import (
    Stack,
    aws_verifiedpermissions as verifiedpermissions,
    CfnOutput,
)
from constructs import Construct
import json
from pathlib import Path


class AuthorizationStack(Stack):
    """
    Authorization infrastructure stack
    
    Creates:
    - Amazon Verified Permissions Policy Store
    - Cedar policies for role-based access control
    """

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Define Cedar schema for authorization entities
        cedar_schema = {
            "DocumentManagement": {
                "entityTypes": {
                    "User": {
                        "shape": {
                            "type": "Record",
                            "attributes": {
                                "userId": {"type": "String", "required": True},
                                "role": {"type": "String", "required": True},
                                "department": {"type": "String", "required": True},
                            },
                        },
                    },
                    "Document": {
                        "shape": {
                            "type": "Record",
                            "attributes": {
                                "documentId": {"type": "String", "required": True},
                                "owner": {"type": "String", "required": True},
                                "department": {"type": "String", "required": True},
                                "sharedWith": {
                                    "type": "Set",
                                    "element": {"type": "String"},
                                    "required": False,
                                },
                            },
                        },
                    },
                    "Role": {
                        "shape": {
                            "type": "Record",
                            "attributes": {
                                "roleName": {"type": "String", "required": True},
                                "permissions": {
                                    "type": "Set",
                                    "element": {"type": "String"},
                                    "required": True,
                                },
                            },
                        },
                    },
                },
                "actions": {
                    "read": {},
                    "write": {},
                    "delete": {},
                    "share": {},
                },
            },
        }

        # Create Verified Permissions Policy Store
        self.policy_store = verifiedpermissions.CfnPolicyStore(
            self,
            "DocumentManagementPolicyStore",
            validation_settings=verifiedpermissions.CfnPolicyStore.ValidationSettingsProperty(
                mode="STRICT",
            ),
            schema=verifiedpermissions.CfnPolicyStore.SchemaDefinitionProperty(
                cedar_json=json.dumps(cedar_schema),
            ),
        )

        # Create Cedar policies
        self._create_cedar_policies()

        # Outputs
        CfnOutput(
            self,
            "PolicyStoreId",
            value=self.policy_store.attr_policy_store_id,
            description="Verified Permissions Policy Store ID",
            export_name=f"{construct_id}-PolicyStoreId",
        )

        CfnOutput(
            self,
            "PolicyStoreArn",
            value=self.policy_store.attr_arn,
            description="Verified Permissions Policy Store ARN",
            export_name=f"{construct_id}-PolicyStoreArn",
        )

    def _create_cedar_policies(self) -> None:
        """Create Cedar policies in the Verified Permissions policy store"""
        policies_dir = Path(__file__).parent.parent / "policies"
        
        policy_files = [
            ("AdminFullAccess", "admin_full_access.cedar"),
            ("ManagerDepartmentalAccess", "manager_departmental_access.cedar"),
            ("EditorOwnedSharedAccess", "editor_owned_shared_access.cedar"),
            ("ViewerReadOnlyAccess", "viewer_read_only_access.cedar"),
            ("DocumentOwnerPermissions", "document_owner_permissions.cedar"),
        ]
        
        for policy_id, policy_file in policy_files:
            policy_path = policies_dir / policy_file
            
            with open(policy_path, "r") as f:
                policy_statement = f.read()
            
            verifiedpermissions.CfnPolicy(
                self,
                policy_id,
                policy_store_id=self.policy_store.attr_policy_store_id,
                definition=verifiedpermissions.CfnPolicy.PolicyDefinitionProperty(
                    static=verifiedpermissions.CfnPolicy.StaticPolicyDefinitionProperty(
                        statement=policy_statement,
                    ),
                ),
            )
