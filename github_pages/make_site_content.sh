#!/usr/bin/env bash

# This script generates the content for this repository's GitHub Pages site.  It is invoked by the CI and expects the
# working folder to contain:
#   ./monitoring: this repository
#   ./artifacts/monitoring-test-uss_qualifier-*-reports/uss_qualifier/output: Reports generated by from running uss_qualifier
#
# The content placed into ./public by this script will be published to the GitHub Pages site.

mkdir ./public
cp -r ./monitoring/github_pages/static/* ./public

mkdir -p ./public/artifacts/uss_qualifier/reports
for d in ./artifacts/monitoring-test-uss_qualifier-*-reports/ ; do
  echo "$d"
  cp -r "${d}uss_qualifier/output/"* ./public/artifacts/uss_qualifier/reports
done
