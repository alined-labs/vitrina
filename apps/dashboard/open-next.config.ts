import { defineCloudflareConfig } from "@opennextjs/cloudflare";

// No incremental cache: every page reads live data from the API, nothing is prerendered for reuse.
export default defineCloudflareConfig({});
