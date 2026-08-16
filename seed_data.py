from sample_data.seed import seed


if __name__ == "__main__":
    counts = seed()
    print(f"Seeded sample data: {counts['assets']} assets, {counts['infrastructure']} infrastructure items.")
