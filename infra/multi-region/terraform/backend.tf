# Remote, locked, encrypted state (#315).
#
# Deliberately empty, as in the single-region stack (infra/terraform/versions.tf):
# the bucket, key, table and region are supplied at init with -backend-config,
# so this file carries no account-specific names and a missing configuration
# fails the init rather than silently writing state to the agent's disk. See
# infra/multi-region/jenkins/Jenkinsfile, and README.md for running it by hand.
terraform {
  backend "s3" {}
}
