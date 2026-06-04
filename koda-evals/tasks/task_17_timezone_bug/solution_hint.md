# Solution
Compare both dates in UTC: convert naive datetimes with `.replace(tzinfo=timezone.utc)` and aware datetimes with `.astimezone(timezone.utc)`.
