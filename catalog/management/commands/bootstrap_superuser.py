import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update a default superuser from environment variables."

    def handle(self, *args, **options):
        username = os.getenv("DJANGO_SUPERUSER_USERNAME", "admin")
        email = os.getenv("DJANGO_SUPERUSER_EMAIL", "admin@example.com")
        password = os.getenv("DJANGO_SUPERUSER_PASSWORD", "")
        if not password:
            raise CommandError(
                "DJANGO_SUPERUSER_PASSWORD is not set. Set it in .env before running this command; "
                "there is deliberately no default, because this command grants superuser access."
            )

        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={
                "email": email,
                "is_staff": True,
                "is_superuser": True,
            },
        )

        if created:
            user.set_password(password)
            user.save()
            self.stdout.write(self.style.SUCCESS(f"Created superuser '{username}'."))
            return

        updated = False
        if not user.is_staff:
            user.is_staff = True
            updated = True
        if not user.is_superuser:
            user.is_superuser = True
            updated = True
        if user.email != email:
            user.email = email
            updated = True

        user.set_password(password)
        user.save()

        message = f"Updated existing user '{username}'." if updated else f"Reset password for existing user '{username}'."
        self.stdout.write(self.style.SUCCESS(message))
