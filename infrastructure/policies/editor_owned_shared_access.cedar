// Cedar Policy: Editor Owned/Shared Document Access
// Editor users can read and write documents they own or have been shared with

permit(
    principal,
    action in [
        DocumentManagement::Action::"read",
        DocumentManagement::Action::"write"
    ],
    resource
)
when {
    principal has role &&
    principal.role == "Editor" &&
    principal has userId &&
    resource has owner &&
    (
        resource.owner == principal.userId ||
        (resource has sharedWith && resource.sharedWith.contains(principal.userId))
    )
};
