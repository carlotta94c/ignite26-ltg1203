#!/usr/bin/env sh

set -eu

required_env() {
  value="$(azd env get-value "$1")" || {
    printf 'Failed to read required azd environment value %s.\n' "$1" >&2
    return 1
  }
  if [ -z "$value" ]; then
    printf 'Required azd environment value %s is empty.\n' "$1" >&2
    return 1
  fi
  printf '%s' "$value"
}

project_endpoint="$(required_env FOUNDRY_PROJECT_ENDPOINT)"
project_id="$(required_env AZURE_AI_PROJECT_ID)"
agent_name="$(required_env AGENT_CSV_CHART_ANALYST_HOSTED_NAME)"
role_id="53ca6127-db72-4b80-b1b0-d745d6d5456d"

agent_principal_id="$(az rest \
  --method GET \
  --url "${project_endpoint}/agents/${agent_name}?api-version=v1" \
  --resource https://ai.azure.com \
  --query instance_identity.principal_id \
  --output tsv)"

if [ -z "$agent_principal_id" ]; then
  printf 'Failed to retrieve the hosted agent identity principal ID.\n' >&2
  exit 1
fi

assignment_count="$(az role assignment list \
  --assignee-object-id "$agent_principal_id" \
  --role "$role_id" \
  --scope "$project_id" \
  --include-inherited \
  --fill-principal-name false \
  --query 'length(@)' \
  --output tsv)"

case "$assignment_count" in
  ''|*[!0-9]*)
    printf 'Failed to read the Foundry User role assignment count.\n' >&2
    exit 1
    ;;
esac

if [ "$assignment_count" = "0" ]; then
  az role assignment create \
    --assignee-object-id "$agent_principal_id" \
    --assignee-principal-type ServicePrincipal \
    --role "$role_id" \
    --scope "$project_id" \
    --output none
fi

printf 'Foundry User is assigned to hosted agent identity %s at project scope or an ancestor.\n' "$agent_principal_id"
