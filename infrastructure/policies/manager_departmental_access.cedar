// Cedar Policy: Manager Departmental Access
// Requirements: 2.3
// Manager users can manage documents in their department

permit(
    principal,
    action in [
        DocumentManagement::Action::"read",
        DocumentManagement::Action::"write",
        DocumentManagement::Action::"delete",
        DocumentManagement::Action::"share"
    ],
    resource
)
when {
    principal has role &&
    principal.role == "Manager" &&
    principal has department &&
    resource has department &&
    principal.department == resource.department
};
