# Security and privacy

Keep credentials in deployment environment variables or a secret store. Never commit real .env files, NAS paths, camera serials, private addresses, captured labels, logs or databases. Use generic placeholders in examples and redact reports before sharing.

Camera OCR runs locally. Masterlists are user-maintained; observations never update them automatically. The UI password protects camera previews and control endpoints. Use a trusted LAN or an HTTPS reverse proxy for remote access.

Report suspected credential exposure privately through GitHub's private vulnerability reporting if enabled. Otherwise contact the maintainer privately; do not post credentials or private captures in an issue. Rotate any exposed credential even after removing it from Git.

A source audit cannot guarantee absence of every secret. Git history, cached commits, Actions logs/artifacts and published image versions require separate review before a private project is made public.
