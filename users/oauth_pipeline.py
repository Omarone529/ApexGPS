from .models import CustomUser, UserRoles

def create_user_profile(backend, user, response, *args, **kwargs):
    """
    Pipeline function to set user role and other profile data
    after social auth creation.
    """
    if not user:
        return
    if kwargs.get("is_new"):
        user.role = UserRoles.SUBSCRIBED
        if response.get("given_name"):
            user.first_name = response.get("given_name", "")
        if response.get("family_name"):
            user.last_name = response.get("family_name", "")
        if response.get("picture"):
            user.profile_picture = response.get("picture")

        user.save()