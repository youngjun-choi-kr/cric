# Running the CRIC watch loop on Nebius AI Cloud

`cric watch` is the unattended loop: every cycle it pulls a fresh CelesTrak catalog (active satellites and public
debris), screens the next 24 h, and runs the full incident pipeline for any encounter with our fleet
(`fixtures/fleet.json`) closer than 1 km that still leaves two hours to act. Anything other than MONITOR is
posted to `CRIC_WEBHOOK_URL` for a person. It never commands a spacecraft.

Models run on Nebius Token Factory; the loop itself needs only a small CPU VM, so it fits a
[Serverless AI Job](https://docs.nebius.com/serverless/jobs/manage). Jobs run up to 168 h and have no schedule
flag, so the job runs the loop for its whole lifetime instead of being started by a scheduler.

## 1. Build and push the image

```bash
docker build --platform linux/amd64 -t cric .
docker tag cric cr.<region>.nebius.cloud/<registry-id>/cric:latest
docker push cr.<region>.nebius.cloud/<registry-id>/cric:latest
```

Use the registry path shown for your Container Registry in the Nebius console.

## 2. Store the keys in SecretStash

`NEBIUS_API_KEY` (Token Factory), `TAVILY_API_KEY`, and optionally `CRIC_WEBHOOK_URL` (Slack, Teams or any
endpoint that accepts JSON).

## 3. Create the job

```bash
nebius ai job create \
  --name cric-watch \
  --image cr.<region>.nebius.cloud/<registry-id>/cric:latest \
  --container-command cric \
  --args "watch --every 30" \
  --env-secret "NEBIUS_API_KEY=<secret>,TAVILY_API_KEY=<secret>,CRIC_WEBHOOK_URL=<secret>" \
  --volume "s3://<bucket>:/data:rw" \
  --platform <cpu-platform> --preset <small-cpu-preset> \
  --disk-size 20Gi \
  --timeout 168h \
  --restart-policy on-failure
```

CPU-only VMs must be on-demand (not preemptible). Check `nebius ai job create --help` for the platform and
preset names available in your project. The `/data` volume keeps `watch/seen.json` (so a restart does not
re-open handled encounters), `watch/log.jsonl`, and every incident JSON, which the console can replay.

## 4. Check it

```bash
nebius ai job list
nebius ai job logs <job-id>
```

Each cycle logs one line (`objects screened, encounters, for our fleet`) and one JSON line per incident.

## Try it locally first

```bash
docker run --env-file .env -v "$PWD/data:/data" cric watch --cycles 1 --dry-run
```

`--dry-run` screens and selects without model calls. Status: the loop runs locally (`cric watch --dry-run`
found the IRIDIUM 149 encounter from the snapshot); the Docker image has not been built yet, and we
have not run it on Nebius AI Cloud (it needs an AI Cloud account and VM quota, beyond the hackathon's Token
Factory credits).
