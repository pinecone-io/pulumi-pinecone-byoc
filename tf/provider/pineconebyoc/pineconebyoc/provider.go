package pineconebyoc

import (
	"context"

	"github.com/hashicorp/terraform-plugin-sdk/v2/diag"
	"github.com/hashicorp/terraform-plugin-sdk/v2/helper/schema"
)

func Provider(version string) func() *schema.Provider {
	return func() *schema.Provider {
		p := &schema.Provider{
			Schema: map[string]*schema.Schema{
				"api_url": {
					Type:        schema.TypeString,
					Optional:    true,
					DefaultFunc: schema.EnvDefaultFunc("PINECONE_API_URL", "https://api.pinecone.io"),
					Description: "Pinecone API URL.",
				},
				"pinecone_api_key": {
					Type:        schema.TypeString,
					Optional:    true,
					Sensitive:   true,
					DefaultFunc: schema.EnvDefaultFunc("PINECONE_API_KEY", nil),
					Description: "Pinecone API key used for bootstrap resources.",
				},
			},
			ResourcesMap: map[string]*schema.Resource{
				"pineconebyoc_environment":                       resourceEnvironment(),
				"pineconebyoc_cpgw_api_key":                      resourceCpgwAPIKey(),
				"pineconebyoc_service_account":                   resourceServiceAccount(),
				"pineconebyoc_project_api_key":                   resourceProjectAPIKey(),
				"pineconebyoc_dns_delegation":                    resourceDNSDelegation(),
				"pineconebyoc_datadog_api_key":                   resourceDatadogAPIKey(),
				"pineconebyoc_amp_access":                        resourceAmpAccess(),
				"pineconebyoc_cluster_uninstaller":               resourceClusterUninstaller(),
				"pineconebyoc_kubernetes_retained_namespace":     resourceKubernetesRetainedNamespace(),
				"pineconebyoc_aws_alb_waiter":                    resourceAWSALBWaiter(),
				"pineconebyoc_aws_vpc_endpoint_dns_verification": resourceAWSVPCEndpointDNSVerification(),
				"pineconebyoc_gcp_forwarding_rule_waiter":        resourceGCPForwardingRuleWaiter(),
				"pineconebyoc_gcp_forwarding_rule_delete_waiter": resourceGCPForwardingRuleDeleteWaiter(),
				"pineconebyoc_aks_api_server_waiter":             resourceAKSAPIWaiter(),
			},
		}

		p.ConfigureContextFunc = func(ctx context.Context, d *schema.ResourceData) (any, diag.Diagnostics) {
			return newClient(d.Get("api_url").(string), d.Get("pinecone_api_key").(string)), nil
		}
		return p
	}
}

func configuredClient(meta any, d *schema.ResourceData) *Client {
	base := meta.(*Client)
	apiURL := base.APIURL
	if v, ok := d.GetOk("api_url"); ok {
		apiURL = v.(string)
	}
	key := base.PineconeAPIKey
	if v, ok := d.GetOk("pinecone_api_key"); ok {
		key = v.(string)
	}
	return newClient(apiURL, key)
}

func apiURLSchema() *schema.Schema {
	return &schema.Schema{Type: schema.TypeString, Optional: true, Default: "https://api.pinecone.io", ForceNew: true}
}

func pineconeAPIKeySchema() *schema.Schema {
	return &schema.Schema{Type: schema.TypeString, Optional: true, Sensitive: true, ForceNew: true}
}
