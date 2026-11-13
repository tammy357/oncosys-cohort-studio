"""Tests never call a network service or read a developer's .env: the hosted AI service is off and the repository's
.env is ignored. Tests that need a model use a scripted stand-in, and tests of the AI route clear the key variables."""
import os

os.environ["COHORT_STUDIO_HOSTED"] = "off"
os.environ["COHORT_STUDIO_NO_DOTENV"] = "1"
