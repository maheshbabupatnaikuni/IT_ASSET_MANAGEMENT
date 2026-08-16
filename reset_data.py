import os


os.environ.setdefault("ITAMS_ALLOW_EPHEMERAL_SECRET", "true")
os.environ.setdefault("ITAMS_ADMIN_PASSWORD", "admin123")  # local-only credential

from sample_data.reset import reset


if __name__ == "__main__":
    counts = reset()
    print(f"Reset complete: {counts['assets']} assets, {counts['infrastructure']} infrastructure items.")
