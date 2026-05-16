package pineconebyoc

import (
	"context"
	"encoding/json"
	"fmt"
	"net"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/hashicorp/terraform-plugin-sdk/v2/diag"
	"github.com/hashicorp/terraform-plugin-sdk/v2/helper/schema"
)

func resourceAWSALBWaiter() *schema.Resource {
	return &schema.Resource{
		CreateContext: waitAWSALB,
		ReadContext:   readNoop,
		UpdateContext: waitAWSALB,
		DeleteContext: deleteNoop,
		Schema: map[string]*schema.Schema{
			"name":           {Type: schema.TypeString, Required: true},
			"region":         {Type: schema.TypeString, Required: true},
			"arn":            {Type: schema.TypeString, Computed: true},
			"dns_name":       {Type: schema.TypeString, Computed: true},
			"hosted_zone_id": {Type: schema.TypeString, Computed: true},
		},
	}
}

func waitAWSALB(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	name := d.Get("name").(string)
	region := d.Get("region").(string)
	var last string
	for i := 0; i < 120; i++ {
		out, err := exec.CommandContext(ctx, "aws", "elbv2", "describe-load-balancers", "--names", name, "--region", region, "--output", "json").CombinedOutput()
		last = string(out)
		if err == nil {
			var doc struct {
				LoadBalancers []struct {
					ARN                   string `json:"LoadBalancerArn"`
					DNSName               string `json:"DNSName"`
					CanonicalHostedZoneID string `json:"CanonicalHostedZoneId"`
				} `json:"LoadBalancers"`
			}
			if json.Unmarshal(out, &doc) == nil && len(doc.LoadBalancers) > 0 {
				lb := doc.LoadBalancers[0]
				d.SetId(lb.ARN)
				_ = d.Set("arn", lb.ARN)
				_ = d.Set("dns_name", lb.DNSName)
				_ = d.Set("hosted_zone_id", lb.CanonicalHostedZoneID)
				return nil
			}
		}
		time.Sleep(10 * time.Second)
	}
	return diag.Errorf("failed waiting for AWS ALB %q: %s", name, last)
}

func resourceAWSVPCEndpointDNSVerification() *schema.Resource {
	return &schema.Resource{
		CreateContext: waitAWSVPCEndpointDNS,
		ReadContext:   readNoop,
		UpdateContext: waitAWSVPCEndpointDNS,
		DeleteContext: deleteNoop,
		Schema: map[string]*schema.Schema{
			"service_id":   {Type: schema.TypeString, Required: true},
			"service_name": {Type: schema.TypeString, Required: true},
			"region":       {Type: schema.TypeString, Required: true},
		},
	}
}

func waitAWSVPCEndpointDNS(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	serviceID := d.Get("service_id").(string)
	region := d.Get("region").(string)
	time.Sleep(60 * time.Second)
	for i := 0; i < 120; i++ {
		if i%3 == 0 {
			_ = exec.CommandContext(ctx, "aws", "ec2", "start-vpc-endpoint-service-private-dns-verification", "--service-id", serviceID, "--region", region).Run()
		}
		out, err := exec.CommandContext(ctx, "aws", "ec2", "describe-vpc-endpoint-service-configurations", "--service-ids", serviceID, "--region", region, "--output", "json").CombinedOutput()
		if err == nil {
			var doc struct {
				ServiceConfigurations []struct {
					PrivateDNSNameConfiguration struct {
						State string `json:"State"`
					} `json:"PrivateDnsNameConfiguration"`
				} `json:"ServiceConfigurations"`
			}
			if json.Unmarshal(out, &doc) == nil && len(doc.ServiceConfigurations) > 0 {
				state := doc.ServiceConfigurations[0].PrivateDNSNameConfiguration.State
				if state == "verified" {
					d.SetId(serviceID)
					return nil
				}
				if state == "failed" {
					return diag.Errorf("private DNS verification failed for %s", serviceID)
				}
			}
		}
		time.Sleep(10 * time.Second)
	}
	return diag.Errorf("timeout waiting for private DNS verification for %s", serviceID)
}

func resourceGCPForwardingRuleWaiter() *schema.Resource {
	return &schema.Resource{
		CreateContext: waitGCPForwardingRule,
		ReadContext:   readNoop,
		UpdateContext: waitGCPForwardingRule,
		DeleteContext: deleteNoop,
		Schema: map[string]*schema.Schema{
			"project":    {Type: schema.TypeString, Required: true},
			"region":     {Type: schema.TypeString, Required: true},
			"cell_name":  {Type: schema.TypeString, Required: true},
			"ip_address": {Type: schema.TypeString, Computed: true},
			"self_link":  {Type: schema.TypeString, Computed: true},
		},
	}
}

func waitGCPForwardingRule(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	project := d.Get("project").(string)
	region := d.Get("region").(string)
	cellName := d.Get("cell_name").(string)
	for i := 0; i < 120; i++ {
		out, err := gcloudJSONOutput(ctx, "compute", "forwarding-rules", "list", "--regions", region, "--project", project, "--format=json")
		if err == nil {
			var rules []struct {
				Name       string `json:"name"`
				IPAddress  string `json:"IPAddress"`
				SelfLink   string `json:"selfLink"`
				Subnetwork string `json:"subnetwork"`
			}
			if json.Unmarshal(out, &rules) == nil {
				for _, r := range rules {
					if strings.HasSuffix(r.Subnetwork, cellName) || strings.Contains(r.Name, cellName) {
						d.SetId(r.SelfLink)
						_ = d.Set("ip_address", r.IPAddress)
						_ = d.Set("self_link", r.SelfLink)
						return nil
					}
				}
			}
		}
		time.Sleep(10 * time.Second)
	}
	return diag.Errorf("timeout waiting for GCP forwarding rule for %s", cellName)
}

func resourceGCPForwardingRuleDeleteWaiter() *schema.Resource {
	return &schema.Resource{
		CreateContext: func(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
			d.SetId(fmt.Sprintf("%s/%s/%s", d.Get("project").(string), d.Get("region").(string), d.Get("cell_name").(string)))
			return nil
		},
		ReadContext:   readNoop,
		UpdateContext: updateNoop,
		DeleteContext: waitGCPForwardingRuleDeleted,
		Schema: map[string]*schema.Schema{
			"project":   {Type: schema.TypeString, Required: true},
			"region":    {Type: schema.TypeString, Required: true},
			"cell_name": {Type: schema.TypeString, Required: true},
		},
	}
}

func waitGCPForwardingRuleDeleted(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	project := d.Get("project").(string)
	region := d.Get("region").(string)
	cellName := d.Get("cell_name").(string)
	for i := 0; i < 120; i++ {
		out, err := gcloudJSONOutput(ctx, "compute", "forwarding-rules", "list", "--regions", region, "--project", project, "--format=json")
		if err != nil {
			return diag.Errorf("failed listing GCP forwarding rules while waiting for %s deletion: %v\n%s", cellName, err, out)
		}
		var rules []struct {
			Name       string `json:"name"`
			Subnetwork string `json:"subnetwork"`
		}
		if err := json.Unmarshal(out, &rules); err != nil {
			return diag.Errorf("failed parsing GCP forwarding rule list while waiting for %s deletion: %v", cellName, err)
		}
		found := false
		for _, r := range rules {
			if strings.HasSuffix(r.Subnetwork, cellName) || strings.Contains(r.Name, cellName) {
				found = true
				break
			}
		}
		if !found {
			return nil
		}
		time.Sleep(10 * time.Second)
	}
	return diag.Errorf("timeout waiting for GCP forwarding rule for %s to be deleted", cellName)
}

func gcloudJSONOutput(ctx context.Context, args ...string) ([]byte, error) {
	cmd := exec.CommandContext(ctx, "gcloud", args...)
	withCommonToolPath(cmd)
	out, err := cmd.Output()
	if err != nil {
		if exitErr, ok := err.(*exec.ExitError); ok && len(exitErr.Stderr) > 0 {
			return out, fmt.Errorf("%w: %s", err, strings.TrimSpace(string(exitErr.Stderr)))
		}
		return out, err
	}
	return out, nil
}

func resourceAKSAPIWaiter() *schema.Resource {
	return &schema.Resource{
		CreateContext: waitAKSAPI,
		ReadContext:   readNoop,
		UpdateContext: waitAKSAPI,
		DeleteContext: deleteNoop,
		Schema: map[string]*schema.Schema{
			"kubeconfig": {Type: schema.TypeString, Required: true, Sensitive: true},
		},
	}
}

func waitAKSAPI(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	host := serverHost(d.Get("kubeconfig").(string))
	for i := 0; i < 120; i++ {
		if _, err := net.LookupHost(host); err == nil {
			d.SetId(host)
			return nil
		}
		time.Sleep(10 * time.Second)
	}
	return diag.Errorf("timeout waiting for AKS API server DNS %s", host)
}

func resourceClusterUninstaller() *schema.Resource {
	return &schema.Resource{
		CreateContext: func(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
			d.SetId("uninstaller-ready")
			return nil
		},
		ReadContext:   readNoop,
		UpdateContext: updateNoop,
		DeleteContext: runClusterUninstall,
		Schema: map[string]*schema.Schema{
			"kubeconfig":      {Type: schema.TypeString, Required: true, Sensitive: true},
			"pinetools_image": {Type: schema.TypeString, Required: true},
			"cloud":           {Type: schema.TypeString, Required: true},
		},
	}
}

func resourceKubernetesRetainedNamespace() *schema.Resource {
	return &schema.Resource{
		CreateContext: upsertKubernetesRetainedNamespace,
		ReadContext:   readNoop,
		UpdateContext: upsertKubernetesRetainedNamespace,
		DeleteContext: deleteNoop,
		Schema: map[string]*schema.Schema{
			"kubeconfig": {Type: schema.TypeString, Required: true, Sensitive: true},
			"cloud":      {Type: schema.TypeString, Optional: true, Default: ""},
			"name":       {Type: schema.TypeString, Required: true, ForceNew: true},
			"labels":     {Type: schema.TypeMap, Optional: true, Elem: &schema.Schema{Type: schema.TypeString}},
		},
	}
}

func upsertKubernetesRetainedNamespace(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	kubeconfigPath, cleanup, err := writeKubeconfig(ctx, d.Get("kubeconfig").(string), d.Get("cloud").(string))
	if err != nil {
		return diag.FromErr(err)
	}
	defer cleanup()

	var labels strings.Builder
	for k, v := range d.Get("labels").(map[string]any) {
		labels.WriteString(fmt.Sprintf("    %s: %s\n", k, v.(string)))
	}

	manifest := fmt.Sprintf(`apiVersion: v1
kind: Namespace
metadata:
  name: %s
  labels:
%s`, d.Get("name").(string), labels.String())

	cmd := kubectl(ctx, kubeconfigPath, "apply", "-f", "-")
	cmd.Stdin = strings.NewReader(manifest)
	if out, err := cmd.CombinedOutput(); err != nil {
		return diag.Errorf("failed applying retained namespace: %v\n%s", err, out)
	}

	d.SetId(d.Get("name").(string))
	return nil
}

func runClusterUninstall(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	kubeconfigPath, cleanup, err := writeKubeconfig(ctx, d.Get("kubeconfig").(string), d.Get("cloud").(string))
	if err != nil {
		return diag.FromErr(err)
	}
	defer cleanup()

	if d.Get("cloud").(string) == "azure" {
		_ = kubectl(ctx, kubeconfigPath, "delete", "pdb", "--all", "--all-namespaces").Run()
	}

	jobName := fmt.Sprintf("pinetools-uninstall-%d", time.Now().Unix())
	manifest := fmt.Sprintf(`apiVersion: batch/v1
kind: Job
metadata:
  name: %s
  namespace: pc-control-plane
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 1800
  ttlSecondsAfterFinished: 3600
  template:
    spec:
      serviceAccountName: pinetools
      restartPolicy: Never
      tolerations:
      - key: node.kubernetes.io/disk-pressure
        operator: Exists
        effect: NoSchedule
      containers:
      - name: pinetools
        image: %s
        command: ["/bin/sh", "-c"]
        args: ["pinetools cluster uninstall --force"]
        resources:
          requests:
            ephemeral-storage: 1Gi
            memory: 512Mi
            cpu: 100m
          limits:
            ephemeral-storage: 5Gi
            memory: 2Gi
`, jobName, d.Get("pinetools_image").(string))

	cmd := kubectl(ctx, kubeconfigPath, "apply", "-f", "-")
	cmd.Stdin = strings.NewReader(manifest)
	if out, err := cmd.CombinedOutput(); err != nil {
		return diag.Errorf("failed creating uninstall job: %v\n%s", err, out)
	}
	if err := waitForUninstallJob(ctx, kubeconfigPath, jobName); err != nil {
		logs, _ := kubectl(ctx, kubeconfigPath, "logs", "-n", "pc-control-plane", "job/"+jobName, "--all-containers=true").CombinedOutput()
		return diag.Errorf("%v\nlogs:\n%s", err, logs)
	}

	cleanupClusterAdmission(ctx, kubeconfigPath)
	return nil
}

func writeKubeconfig(ctx context.Context, kubeconfig, cloud string) (string, func(), error) {
	dir, err := os.MkdirTemp("", "pinecone-byoc-kubeconfig-*")
	if err != nil {
		return "", nil, err
	}
	cleanup := func() { _ = os.RemoveAll(dir) }
	kubeconfigPath := filepath.Join(dir, "config")
	if cloud == "gcp" {
		refreshed, err := refreshGCPKubeconfigToken(ctx, kubeconfig)
		if err != nil {
			cleanup()
			return "", nil, err
		}
		kubeconfig = refreshed
	}
	if err := os.WriteFile(kubeconfigPath, []byte(kubeconfig), 0600); err != nil {
		cleanup()
		return "", nil, err
	}
	return kubeconfigPath, cleanup, nil
}

func waitForUninstallJob(ctx context.Context, kubeconfigPath, jobName string) error {
	deadline := time.Now().Add(1800 * time.Second)
	for time.Now().Before(deadline) {
		out, err := kubectl(ctx, kubeconfigPath, "get", "job", jobName, "-n", "pc-control-plane", "-o", "json").CombinedOutput()
		if err != nil {
			return fmt.Errorf("failed reading uninstall job %s: %w\n%s", jobName, err, out)
		}
		var doc struct {
			Status struct {
				Active     int `json:"active"`
				Succeeded  int `json:"succeeded"`
				Failed     int `json:"failed"`
				Conditions []struct {
					Type    string `json:"type"`
					Status  string `json:"status"`
					Reason  string `json:"reason"`
					Message string `json:"message"`
				} `json:"conditions"`
			} `json:"status"`
		}
		if err := json.Unmarshal(out, &doc); err != nil {
			return fmt.Errorf("failed parsing uninstall job %s: %w", jobName, err)
		}
		for _, condition := range doc.Status.Conditions {
			if condition.Type == "Complete" && condition.Status == "True" {
				return nil
			}
			if condition.Type == "Failed" && condition.Status == "True" {
				return fmt.Errorf("uninstall job %s failed: %s %s", jobName, condition.Reason, condition.Message)
			}
		}
		if doc.Status.Succeeded > 0 {
			return nil
		}
		if doc.Status.Failed > 0 {
			return fmt.Errorf("uninstall job %s failed after %d failed pod(s)", jobName, doc.Status.Failed)
		}
		time.Sleep(10 * time.Second)
	}
	return fmt.Errorf("uninstall job %s timed out after 1800s", jobName)
}

func kubectl(ctx context.Context, kubeconfig string, args ...string) *exec.Cmd {
	full := append([]string{"--kubeconfig", kubeconfig}, args...)
	cmd := exec.CommandContext(ctx, "kubectl", full...)
	withCommonToolPath(cmd)
	return cmd
}

func refreshGCPKubeconfigToken(ctx context.Context, kubeconfig string) (string, error) {
	cmd := exec.CommandContext(ctx, "gcloud", "auth", "print-access-token")
	withCommonToolPath(cmd)
	out, err := cmd.Output()
	if err != nil {
		return "", fmt.Errorf("failed refreshing GCP kubeconfig token: %w", err)
	}
	token := strings.TrimSpace(string(out))
	lines := strings.Split(kubeconfig, "\n")
	execBlockLine := -1
	execBlockIndent := 0
	for i, line := range lines {
		trimmed := strings.TrimSpace(line)
		if strings.HasPrefix(trimmed, "token:") {
			indent := line[:len(line)-len(strings.TrimLeft(line, " \t"))]
			lines[i] = indent + "token: " + token
			return strings.Join(lines, "\n"), nil
		}
		if trimmed == "exec:" {
			execBlockLine = i
			execBlockIndent = len(line) - len(strings.TrimLeft(line, " \t"))
		}
	}
	if execBlockLine >= 0 {
		end := execBlockLine + 1
		for end < len(lines) {
			line := lines[end]
			if strings.TrimSpace(line) == "" {
				end++
				continue
			}
			indent := len(line) - len(strings.TrimLeft(line, " \t"))
			if indent <= execBlockIndent {
				break
			}
			end++
		}
		tokenLine := strings.Repeat(" ", execBlockIndent) + "token: " + token
		replaced := append([]string{}, lines[:execBlockLine]...)
		replaced = append(replaced, tokenLine)
		replaced = append(replaced, lines[end:]...)
		return strings.Join(replaced, "\n"), nil
	}
	return kubeconfig, nil
}

func withCommonToolPath(cmd *exec.Cmd) {
	pathEntries := []string{
		"/opt/homebrew/share/google-cloud-sdk/bin",
		"/usr/local/share/google-cloud-sdk/bin",
		"/opt/google-cloud-sdk/bin",
	}
	if home, err := os.UserHomeDir(); err == nil && home != "" {
		pathEntries = append(pathEntries, filepath.Join(home, "google-cloud-sdk", "bin"))
	}
	if current := os.Getenv("PATH"); current != "" {
		pathEntries = append(pathEntries, current)
	}
	pathValue := "PATH=" + strings.Join(pathEntries, string(os.PathListSeparator))
	env := os.Environ()
	for i, item := range env {
		if strings.HasPrefix(item, "PATH=") {
			env[i] = pathValue
			cmd.Env = env
			return
		}
	}
	cmd.Env = append(env, pathValue)
}

func cleanupClusterAdmission(ctx context.Context, kubeconfigPath string) {
	for _, name := range []string{
		"externalsecret-validate",
		"secretstore-validate",
		"clustersecretstore-validate",
		"pushsecret-validate",
	} {
		_ = kubectl(ctx, kubeconfigPath, "delete", "validatingwebhookconfiguration", name, "--ignore-not-found").Run()
	}
	_ = kubectl(ctx, kubeconfigPath, "delete", "apiservice", "v1beta1.metrics.k8s.io", "--ignore-not-found").Run()
}

func serverHost(kubeconfig string) string {
	for _, line := range strings.Split(kubeconfig, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "server:") {
			v := strings.TrimSpace(strings.TrimPrefix(line, "server:"))
			v = strings.TrimPrefix(v, "https://")
			v = strings.TrimPrefix(v, "http://")
			return strings.Split(strings.Split(v, "/")[0], ":")[0]
		}
	}
	return ""
}

func deleteNoop(ctx context.Context, d *schema.ResourceData, meta any) diag.Diagnostics {
	return nil
}
