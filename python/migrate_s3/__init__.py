"""NAS-to-AWS (S3) migration engine.

Stdlib + boto3 only. The target is S3, reached through an endpoint URL.
That URL is the single swap point:
  LocalStack: http://localhost:4566 (Docker, primary - see docker-compose.yml)
  moto:       http://localhost:5000 (no Docker, used to run this in the VM)
  Real AWS:   None (no endpoint_url - boto3 uses the real AWS endpoint)
The sync, checksum, ACL and quota logic does not know which one it is.
"""

__version__ = "1.0.0"
