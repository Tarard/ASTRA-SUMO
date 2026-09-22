# Prompt

```text
Use Torii to clean the Ingolstadt city-center network around https://www.openstreetmap.org/#map=17/48.765391/11.423800 from OSM, compare it with the TUM-VT/sumo_ingolstadt cleaned network for the same bbox, and open the cleaned network in Netedit.
```

Required behavior:

- Use OSM as the construction source.
- Use the TUM `sumo_ingolstadt` network only as a manually cleaned reference comparator.
- Ask for the target road-detail level before treating the network as an experiment candidate; for the TUM comparison, use `network_profile='reference_matched'` with the TUM `.net.xml` as the reference artifact.
- Inspect TUM's retained road hierarchy and lane permissions before making the comparison.
- Cut the TUM reference network to the same city-center bbox before comparing.
- Keep Torii's `vehicle_core` routeability layer separate from its `reference_visual_detail` Netedit comparison layer.
- Run connectivity, connected-core, topology, routeability, and Netedit launch evidence steps.
- Bound the claim if topology or traffic-light grouping differs from the TUM reference.
- For dense physical-intersection candidates, inspect local cluster-graph fields (`internal_edge_ids`, `boundary_edge_ids`, `connected_node_pairs`, overlap hints, and approach count), then generate Google Maps default-map review links before proposing junction aggregation; satellite view is optional when the road-map geometry is ambiguous.
