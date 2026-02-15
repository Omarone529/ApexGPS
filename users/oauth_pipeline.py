from .models import UserRoles

def create_user_profile(user, response, **kwargs):
    """
    Pipeline function to set user role and other profile data
    after social auth creation.
    """
    if not user:
        return

    # If the user is newly created, assign the subscribed role
    if kwargs.get("is_new"):
        # Assign the subscribed role by default
        user.role = UserRoles.SUBSCRIBED

        if response.get("given_name"):
            user.first_name = response.get("given_name", "")
        if response.get("family_name"):
            user.last_name = response.get("family_name", "")

        user.save()