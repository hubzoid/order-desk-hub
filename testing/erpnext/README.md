# Live ERPNext contract suite

Runs the live adapter against a disposable ERPNext site. Record each run's result, the image digests and the versions, and add the checked majors to `erp/doctor.py` (`QUALIFIED`).

One disposable site, shared by both desks: compose project `hubzoid-erpnext-test`, `http://127.0.0.1:8080`, site `hubzoid-test.localhost`, synthetic data only. Start it only for a run (it takes about 2 GB of memory), and stop it right after.

```bash
export ERPNEXT_ADMIN_PASSWORD=$(openssl rand -hex 16)   # a throwaway test value, kept in this shell only
docker compose -p hubzoid-erpnext-test -f testing/erpnext/compose.yaml up -d --wait
docker compose -p hubzoid-erpnext-test -f testing/erpnext/compose.yaml cp erp/datasets/distributor-v1 backend:/tmp/
docker compose -p hubzoid-erpnext-test -f testing/erpnext/compose.yaml exec -T backend \
  bash -c 'cd /home/frappe/frappe-bench/sites && ../env/bin/python - hubzoid-test.localhost' < testing/erpnext/seed.py
```

The seed creates three users with API keys in `/tmp/hubzoid-erpnext-keys.json` inside the container. Load each into the hub's secret store without printing it, for example `jq -r '."hubzoid-read@synthetic-meridian.example"' | hubzoid secrets set . --ref secret:hub/<hub>/erpnext-read --stdin`. Then the custom field, with the administrator's key (created in the ERPNext UI for the test site):

```bash
hubzoid-erp configure . --file connectors/erpnext.live-test.yaml --expected-revision N
hubzoid-erp bootstrap export . --output output/setup/erpnext-bootstrap.json
hubzoid-erp bootstrap apply . --manifest output/setup/erpnext-bootstrap.json --admin-credential-ref env:ERP_ADMIN_TOKEN
hubzoid-erp doctor .
HUBZOID_ERP_LIVE_SITE=http://127.0.0.1:8080 HUBZOID_ERP_LIVE_READ=secret:hub/<hub>/erpnext-read \
  HUBZOID_ERP_LIVE_DRAFT=secret:hub/<hub>/erpnext-draft HUBZOID_ERP_LIVE_SUBMIT=secret:hub/<hub>/erpnext-submit \
  pytest -m erp_live tests/live/test_erpnext_live.py
docker compose -p hubzoid-erpnext-test -f testing/erpnext/compose.yaml down -v
```

`connectors/erpnext.live-test.yaml` sets `backend: frappe_rest`, `site_url: http://127.0.0.1:8080`, `local_test_site: true` and the three credential references. `local_test_site` is the only way a live adapter talks plain HTTP, and only to loopback. Doctor and every result label the site "LOCAL TEST SITE". A production site needs HTTPS and `local_test_site: false`.
