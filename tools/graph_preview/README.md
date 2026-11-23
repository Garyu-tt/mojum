# Mojum offline graph preview

Open `index.html` directly in a modern browser. No server, package manager,
CDN, network permission, or build step is required.

1. Produce a React Flow or Rete-shaped envelope with
   `mojum.graph_editor_cli export` (see
   [the bridge guide](../../docs/GRAPH_EDITOR_BRIDGE_R01.md)).
2. Open the HTML file and select the JSON envelope.
3. Search by node ID/type and select a node to inspect its visibility, ports,
   config, notes, and imported position.

The page is **read-only**. It never executes graph nodes, writes an edited
layout, or calls the CAS import path. Use the separate bridge CLI for guarded
candidate generation. The page displays the supplied source SHA as an
identifier, not as verified/authenticated evidence.

The viewer accepts at most 512 KiB, 256 nodes, and 2048 edges. All imported
strings enter the DOM through text nodes; there is no HTML rendering, eval,
dynamic module loading, remote asset, or network request. A PRIVATE or
RESTRICTED export is still sensitive: open it only in a trusted local browser
profile and do not publish it.
