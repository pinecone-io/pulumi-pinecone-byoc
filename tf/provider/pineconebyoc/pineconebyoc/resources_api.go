package pineconebyoc

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"time"

	"github.com/hashicorp/terraform-plugin-sdk/v2/diag"
	"github.com/hashicorp/terraform-plugin-sdk/v2/helper/schema"
)

func resourceEnvironment() *schema.Resource {
	return &schema.Resource{
		CreateContext: createEnvironment,
		ReadContext:   readNoop,
		DeleteContext: deleteEnvironment,
		Schema: map[string]*schema.Schema{
			"id":                         {Type: schema.TypeString, Computed: true},
			"env_name":                   {Type: schema.TypeString, Computed: true},
			"org_id":                     {Type: schema.TypeString, Computed: true},
			"org_name":                   {Type: schema.TypeString, Computed: true},
			"cloud":                      {Type: schema.TypeString, Required: true, ForceNew: true},
			"region":                     {Type: schema.TypeString, Required: true, ForceNew: true},
			"global_env":                 {Type: schema.TypeString, Required: true, ForceNew: true},
			"is_public_endpoint_enabled": {Type: schema.TypeBool, Optional: true, Default: true, ForceNew: true},
			"api_url":                    apiURLSchema(),
			"pinecone_api_key":           pineconeAPIKeySchema(),
		},
	}
}

func createEnvironment(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	var out struct {
		ID      string `json:"id"`
		Name    string `json:"name"`
		OrgID   string `json:"org_id"`
		OrgName string `json:"org_name"`
	}
	err := c.do(ctx, http.MethodPost, c.cpgwBootstrapURL()+"/environments", apiKeyHeaders(c.PineconeAPIKey), map[string]any{
		"cloud":                      d.Get("cloud").(string),
		"region":                     d.Get("region").(string),
		"global_env":                 d.Get("global_env").(string),
		"is_public_endpoint_enabled": d.Get("is_public_endpoint_enabled").(bool),
	}, &out)
	if err != nil {
		return diag.FromErr(err)
	}
	d.SetId(out.ID)
	_ = d.Set("id", out.ID)
	_ = d.Set("env_name", out.Name)
	_ = d.Set("org_id", out.OrgID)
	_ = d.Set("org_name", out.OrgName)
	return nil
}

func deleteEnvironment(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	if c.PineconeAPIKey == "" {
		return nil
	}
	err := c.do(ctx, http.MethodDelete, c.cpgwBootstrapURL()+"/environments/"+d.Id(), apiKeyHeaders(c.PineconeAPIKey), nil, nil)
	return diag.FromErr(err)
}

func resourceCpgwAPIKey() *schema.Resource {
	return &schema.Resource{
		CreateContext: createCpgwAPIKey,
		ReadContext:   readNoop,
		DeleteContext: deleteCpgwAPIKey,
		Schema: map[string]*schema.Schema{
			"key_id":           {Type: schema.TypeString, Computed: true},
			"key":              {Type: schema.TypeString, Computed: true, Sensitive: true},
			"environment":      {Type: schema.TypeString, Required: true, ForceNew: true},
			"api_url":          apiURLSchema(),
			"pinecone_api_key": pineconeAPIKeySchema(),
		},
	}
}

func createCpgwAPIKey(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	var out struct {
		ID          string `json:"id"`
		Environment string `json:"environment"`
		Key         string `json:"key"`
	}
	err := c.do(ctx, http.MethodPost, c.cpgwBootstrapURL()+"/cpgw-api-keys", apiKeyHeaders(c.PineconeAPIKey), map[string]any{
		"environment": d.Get("environment").(string),
	}, &out)
	if err != nil {
		return diag.FromErr(err)
	}
	d.SetId(out.ID)
	_ = d.Set("key_id", out.ID)
	_ = d.Set("key", out.Key)
	return nil
}

func deleteCpgwAPIKey(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	err := c.do(ctx, http.MethodDelete, c.cpgwBootstrapURL()+"/cpgw-api-keys/"+d.Id(), apiKeyHeaders(c.PineconeAPIKey), nil, nil)
	return diag.FromErr(err)
}

func resourceServiceAccount() *schema.Resource {
	return &schema.Resource{
		CreateContext: createServiceAccount,
		ReadContext:   readNoop,
		DeleteContext: deleteServiceAccount,
		Schema: map[string]*schema.Schema{
			"name":          {Type: schema.TypeString, Required: true, ForceNew: true},
			"client_id":     {Type: schema.TypeString, Computed: true},
			"client_secret": {Type: schema.TypeString, Computed: true, Sensitive: true},
			"api_url":       apiURLSchema(),
			"cpgw_api_key":  {Type: schema.TypeString, Required: true, Sensitive: true, ForceNew: true},
		},
	}
}

func createServiceAccount(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	key := d.Get("cpgw_api_key").(string)
	var out struct {
		ID           string `json:"id"`
		ClientID     string `json:"client_id"`
		ClientSecret string `json:"client_secret"`
	}
	err := c.do(ctx, http.MethodPost, c.cpgwInfraURL()+"/service-accounts", apiKeyHeaders(key), map[string]any{
		"name": d.Get("name").(string),
	}, &out)
	if err != nil {
		return diag.FromErr(err)
	}
	d.SetId(out.ID)
	_ = d.Set("client_id", out.ClientID)
	_ = d.Set("client_secret", out.ClientSecret)
	return nil
}

func deleteServiceAccount(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	key := d.Get("cpgw_api_key").(string)
	err := c.do(ctx, http.MethodDelete, c.cpgwInfraURL()+"/service-accounts/"+d.Id(), apiKeyHeaders(key), nil, nil)
	return diag.FromErr(err)
}

func resourceProjectAPIKey() *schema.Resource {
	return &schema.Resource{
		CreateContext: createProjectAPIKey,
		ReadContext:   readNoop,
		DeleteContext: deleteProjectAPIKey,
		Schema: map[string]*schema.Schema{
			"org_id":              {Type: schema.TypeString, Required: true, ForceNew: true},
			"project_name":        {Type: schema.TypeString, Required: true, ForceNew: true},
			"key_name":            {Type: schema.TypeString, Required: true, ForceNew: true},
			"api_url":             apiURLSchema(),
			"auth0_domain":        {Type: schema.TypeString, Required: true, ForceNew: true},
			"auth0_client_id":     {Type: schema.TypeString, Required: true, ForceNew: true},
			"auth0_client_secret": {Type: schema.TypeString, Required: true, Sensitive: true, ForceNew: true},
			"api_key_id":          {Type: schema.TypeString, Computed: true},
			"project_id":          {Type: schema.TypeString, Computed: true},
			"value":               {Type: schema.TypeString, Computed: true, Sensitive: true},
		},
	}
}

func createProjectAPIKey(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	token, err := c.accessToken(ctx, auth0Config{
		Domain:       d.Get("auth0_domain").(string),
		ClientID:     d.Get("auth0_client_id").(string),
		ClientSecret: d.Get("auth0_client_secret").(string),
	})
	if err != nil {
		return diag.FromErr(err)
	}
	var project struct {
		ID string `json:"id"`
	}
	err = c.do(ctx, http.MethodPost, fmt.Sprintf("%s/organizations/%s/projects", c.managementURL(), d.Get("org_id").(string)), bearerHeaders(token), map[string]any{
		"name": d.Get("project_name").(string),
	}, &project)
	if err != nil {
		return diag.FromErr(err)
	}
	var key struct {
		Key struct {
			ID        string `json:"id"`
			ProjectID string `json:"project_id"`
		} `json:"key"`
		Value string `json:"value"`
	}
	err = c.do(ctx, http.MethodPost, fmt.Sprintf("%s/projects/%s/api-keys", c.managementURL(), project.ID), bearerHeaders(token), map[string]any{
		"name":  d.Get("key_name").(string),
		"roles": []string{"ProjectEditor"},
	}, &key)
	if err != nil {
		_ = c.do(ctx, http.MethodDelete, c.managementURL()+"/projects/"+project.ID, bearerHeaders(token), nil, nil)
		return diag.FromErr(err)
	}
	d.SetId(project.ID)
	_ = d.Set("project_id", project.ID)
	_ = d.Set("api_key_id", key.Key.ID)
	_ = d.Set("value", key.Value)
	return nil
}

func deleteProjectAPIKey(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	if key := d.Get("value").(string); key != "" {
		if err := c.deleteProjectIndexes(ctx, key, 30*time.Minute, 15*time.Second); err != nil {
			return diag.FromErr(err)
		}
	}
	token, err := c.accessToken(ctx, auth0Config{
		Domain:       d.Get("auth0_domain").(string),
		ClientID:     d.Get("auth0_client_id").(string),
		ClientSecret: d.Get("auth0_client_secret").(string),
	})
	if err != nil {
		return diag.FromErr(err)
	}
	err = c.do(ctx, http.MethodDelete, c.managementURL()+"/projects/"+d.Id(), bearerHeaders(token), nil, nil)
	if isHTTPStatus(err, http.StatusNotFound) {
		return nil
	}
	return diag.FromErr(err)
}

type publicIndex struct {
	Name   string `json:"name"`
	Status struct {
		State string `json:"state"`
	} `json:"status"`
}

func (c *Client) listProjectIndexes(ctx context.Context, projectAPIKey string) ([]publicIndex, error) {
	var out struct {
		Indexes []publicIndex `json:"indexes"`
	}
	err := c.do(ctx, http.MethodGet, c.APIURL+"/indexes", publicAPIHeaders(projectAPIKey), nil, &out)
	return out.Indexes, err
}

func (c *Client) deleteProjectIndexes(ctx context.Context, projectAPIKey string, timeout, pollInterval time.Duration) error {
	deadline := time.Now().Add(timeout)
	for {
		indexes, err := c.listProjectIndexes(ctx, projectAPIKey)
		if err != nil {
			return err
		}
		if len(indexes) == 0 {
			return nil
		}

		for _, index := range indexes {
			err := c.do(ctx, http.MethodDelete, c.APIURL+"/indexes/"+url.PathEscape(index.Name), publicAPIHeaders(projectAPIKey), nil, nil)
			if err != nil && !isHTTPStatus(err, http.StatusNotFound) {
				return err
			}
		}

		if time.Now().After(deadline) {
			names := make([]string, 0, len(indexes))
			for _, index := range indexes {
				names = append(names, fmt.Sprintf("%s(%s)", index.Name, index.Status.State))
			}
			return fmt.Errorf("timed out waiting for Pinecone project indexes to delete before project destroy: %v", names)
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(pollInterval):
		}
	}
}

func resourceDNSDelegation() *schema.Resource {
	return &schema.Resource{
		CreateContext: createDNSDelegation,
		ReadContext:   readNoop,
		UpdateContext: updateDNSDelegation,
		DeleteContext: deleteDNSDelegation,
		Schema: map[string]*schema.Schema{
			"subdomain":    {Type: schema.TypeString, Required: true, ForceNew: true},
			"nameservers":  {Type: schema.TypeList, Required: true, Elem: &schema.Schema{Type: schema.TypeString}},
			"api_url":      apiURLSchema(),
			"cpgw_api_key": {Type: schema.TypeString, Required: true, Sensitive: true},
			"fqdn":         {Type: schema.TypeString, Computed: true},
			"change_id":    {Type: schema.TypeString, Computed: true},
		},
	}
}

func createDNSDelegation(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	return upsertDNSDelegation(ctx, d, meta)
}

func updateDNSDelegation(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	return upsertDNSDelegation(ctx, d, meta)
}

func upsertDNSDelegation(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	var out struct {
		ChangeID string `json:"change_id"`
		Status   string `json:"status"`
		FQDN     string `json:"fqdn"`
	}
	err := c.do(ctx, http.MethodPost, c.cpgwInfraURL()+"/dns-delegation", apiKeyHeaders(d.Get("cpgw_api_key").(string)), map[string]any{
		"subdomain":   d.Get("subdomain").(string),
		"nameservers": stringList(d.Get("nameservers").([]any)),
	}, &out)
	if err != nil {
		return diag.FromErr(err)
	}
	d.SetId(out.FQDN)
	_ = d.Set("fqdn", out.FQDN)
	_ = d.Set("change_id", out.ChangeID)
	return nil
}

func deleteDNSDelegation(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	err := c.do(ctx, http.MethodPost, c.cpgwInfraURL()+"/dns-delegation/delete", apiKeyHeaders(d.Get("cpgw_api_key").(string)), map[string]any{
		"subdomain":   d.Get("subdomain").(string),
		"nameservers": stringList(d.Get("nameservers").([]any)),
	}, nil)
	return diag.FromErr(err)
}

func resourceDatadogAPIKey() *schema.Resource {
	return &schema.Resource{
		CreateContext: createDatadogAPIKey,
		ReadContext:   readNoop,
		UpdateContext: updateNoop,
		DeleteContext: deleteDatadogAPIKey,
		Schema: map[string]*schema.Schema{
			"api_url":      apiURLSchema(),
			"cpgw_api_key": {Type: schema.TypeString, Required: true, Sensitive: true},
			"api_key":      {Type: schema.TypeString, Computed: true, Sensitive: true},
			"key_id":       {Type: schema.TypeString, Computed: true},
		},
	}
}

func createDatadogAPIKey(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	var out struct {
		APIKey string `json:"api_key"`
		KeyID  string `json:"key_id"`
	}
	err := c.do(ctx, http.MethodPost, c.cpgwInfraURL()+"/datadog-credentials", apiKeyHeaders(d.Get("cpgw_api_key").(string)), nil, &out)
	if err != nil {
		return diag.FromErr(err)
	}
	d.SetId(out.KeyID)
	_ = d.Set("api_key", out.APIKey)
	_ = d.Set("key_id", out.KeyID)
	return nil
}

func deleteDatadogAPIKey(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	err := c.do(ctx, http.MethodPost, c.cpgwInfraURL()+"/datadog-credentials/delete", apiKeyHeaders(d.Get("cpgw_api_key").(string)), map[string]any{
		"key_id": d.Id(),
	}, nil)
	return diag.FromErr(err)
}

func resourceAmpAccess() *schema.Resource {
	return &schema.Resource{
		CreateContext: createAmpAccess,
		ReadContext:   readNoop,
		UpdateContext: createAmpAccess,
		DeleteContext: deleteAmpAccess,
		Schema: map[string]*schema.Schema{
			"workload_role_arn":         {Type: schema.TypeString, Required: true},
			"api_url":                   apiURLSchema(),
			"cpgw_api_key":              {Type: schema.TypeString, Required: true, Sensitive: true},
			"pinecone_role_arn":         {Type: schema.TypeString, Computed: true},
			"amp_remote_write_endpoint": {Type: schema.TypeString, Computed: true},
			"amp_region":                {Type: schema.TypeString, Computed: true},
		},
	}
}

func createAmpAccess(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	var last error
	for attempt := 0; attempt < 5; attempt++ {
		if attempt > 0 {
			time.Sleep(time.Duration(1<<attempt) * time.Second)
		}
		var out struct {
			PineconeRoleARN        string `json:"pinecone_role_arn"`
			AmpRemoteWriteEndpoint string `json:"amp_remote_write_endpoint"`
			AmpRegion              string `json:"amp_region"`
		}
		last = c.do(ctx, http.MethodPost, c.cpgwInfraURL()+"/amp-access", apiKeyHeaders(d.Get("cpgw_api_key").(string)), map[string]any{
			"workload_role_arn": d.Get("workload_role_arn").(string),
		}, &out)
		if last == nil {
			d.SetId(d.Get("workload_role_arn").(string))
			_ = d.Set("pinecone_role_arn", out.PineconeRoleARN)
			_ = d.Set("amp_remote_write_endpoint", out.AmpRemoteWriteEndpoint)
			_ = d.Set("amp_region", out.AmpRegion)
			return nil
		}
	}
	return diag.FromErr(last)
}

func deleteAmpAccess(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	c := configuredClient(meta, d)
	err := c.do(ctx, http.MethodPost, c.cpgwInfraURL()+"/amp-access/delete", apiKeyHeaders(d.Get("cpgw_api_key").(string)), nil, nil)
	return diag.FromErr(err)
}

func readNoop(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	return nil
}

func updateNoop(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	return nil
}

func stringList(xs []any) []string {
	out := make([]string, 0, len(xs))
	for _, x := range xs {
		out = append(out, x.(string))
	}
	return out
}
