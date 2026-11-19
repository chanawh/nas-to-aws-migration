# NAS-to-AWS migration factory, common commands.
# Run `make help` for the list.

.PHONY: help test fmt validate demo clean

help:
	@echo "make test      run the 22 unit tests"
	@echo "make fmt       check Terraform formatting"
	@echo "make validate  validate Terraform"
	@echo "make demo      run the full moto demo end to end"
	@echo "make clean     remove Terraform and Python caches"

test:
	cd python && python3 -m unittest discover -s tests -v

fmt:
	cd terraform && terraform fmt -check

validate:
	cd terraform && terraform init -backend=false -input=false && terraform validate

demo:
	bash scripts/run_local_demo.sh

clean:
	rm -rf terraform/.terraform terraform/.terraform.lock.hcl python/migrate_s3/__pycache__ python/tests/__pycache__ python/lambda/__pycache__
