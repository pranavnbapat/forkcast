from django import forms
from django.contrib.auth.forms import AuthenticationForm

from catalog.models import PlannerProfile


class EmailLikeAuthenticationForm(AuthenticationForm):
    username = forms.CharField(
        label="Username",
        widget=forms.TextInput(attrs={"placeholder": "Enter your username", "autofocus": True}),
    )
    password = forms.CharField(
        label="Password",
        strip=False,
        widget=forms.PasswordInput(attrs={"placeholder": "Enter your password"}),
    )


class PlannerProfileForm(forms.ModelForm):
    class Meta:
        model = PlannerProfile
        fields = [
            "primary_goal",
            "secondary_goal",
            "gender",
            "age",
            "height_cm",
            "weight_kg",
            "culture_option",
            "cuisine_option",
            "culture",
            "lifestyle",
            "fasting_pattern",
            "diet_style",
            "allergies",
            "notes",
        ]
        widgets = {
            "culture": forms.TextInput(attrs={"placeholder": "e.g. Gujarati, Cantonese, Kerala, Surinamese"}),
            "fasting_pattern": forms.TextInput(attrs={"placeholder": "e.g. 16:8, Ramadan, none"}),
            "allergies": forms.Textarea(attrs={"rows": 4, "placeholder": "e.g. peanuts, shellfish, lactose, no mushrooms"}),
            "notes": forms.Textarea(attrs={"rows": 4, "placeholder": "e.g. limited cooking time, prefer one-pan meals, need high satiety lunches"}),
        }
