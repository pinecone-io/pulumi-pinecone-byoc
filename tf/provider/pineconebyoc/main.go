package main

import (
	"github.com/hashicorp/terraform-plugin-sdk/v2/plugin"

	"github.com/pinecone-io/terraform-provider-pineconebyoc/pineconebyoc"
)

var version = "0.1.0"

func main() {
	plugin.Serve(&plugin.ServeOpts{
		ProviderFunc: pineconebyoc.Provider(version),
	})
}
