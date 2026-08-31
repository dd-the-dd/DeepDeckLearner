import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, test, vi } from "vitest";

import App from "./App";
import type { CapabilityStatus, Job } from "./api";

const status: CapabilityStatus = {
  controller: { ready: true, version: "test" },
  paths: {
    project: "project",
    trajectory: "trajectory.jsonl",
    checkpoints: "checkpoints",
  },
  sdk: { ready: true },
  torch: { ready: true },
  engine: {
    source_available: false,
    revision: null,
    pinned_revision: "engine-pinned",
    synced: false,
    dirty: false,
    built: false,
    url: "http://127.0.0.1:8787",
    healthy: false,
  },
  pixi: {
    source_available: false,
    built: false,
    build_present: false,
    built_revision: null,
    revision: null,
    pinned_revision: "pixi-pinned",
    synced: false,
    dirty: false,
  },
  hosted: {
    api_key_configured: false,
    trajectory_training: false,
    reason: "Not available yet.",
  },
  workflows: {},
};

const stackJob: Job = {
  id: "stack-job",
  kind: "dependency.stack.prepare",
  label: "Local Engine + Pixi setup",
  argv: [],
  status: "queued",
  created_at: "2026-08-29T00:00:00Z",
  started_at: null,
  finished_at: null,
  exit_code: null,
  artifact_path: null,
  logs: [],
};

function response(body: unknown) {
  return Promise.resolve(
    new Response(JSON.stringify(body), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    }),
  );
}

const resources = {
  system: {
    ramTotalBytes: 16_000_000_000,
    ramUsedBytes: 4_000_000_000,
    ramAvailableBytes: 12_000_000_000,
    gpuTotalBytes: null,
    gpuUsedBytes: null,
  },
  workers: [],
  engine: {
    ramBytes: 0,
    activeLocalGames: 0,
    ramPerGameEstimate: 0,
    attribution: "Shared Engine RSS divided by active local games.",
  },
};

function readResponse(url: string, currentStatus: CapabilityStatus = status) {
  if (url.endsWith("/api/v1/status")) return response(currentStatus);
  if (url.endsWith("/api/v1/models")) return response({ items: [] });
  if (url.endsWith("/api/v1/resources")) return response(resources);
  if (url.endsWith("/api/v1/games")) return response({ items: [] });
  if (url.endsWith("/api/v1/statistics/decks")) return response({ items: [] });
  if (url.endsWith("/api/v1/statistics/training")) return response({ items: [] });
  if (url.endsWith("/api/v1/training/deck-pool")) return response({ decks: [] });
  return response([]);
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("guided onboarding", () => {
  test("opens directly in Agent configuration without a League tab", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        return readResponse(url);
      }),
    );

    render(<App />);

    expect(
      await screen.findByRole("heading", { name: "Agent configuration" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Application setup/i }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "What do you want to do?" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "League" })).not.toBeInTheDocument();
  });

  test("one local setup button requests the composite controller job", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/status")) return response(status);
      if (url.endsWith("/api/v1/session"))
        return response({ token: "local-token" });
      if (url.endsWith("/api/v1/jobs") && init?.method === "POST")
        return response(stackJob);
      return readResponse(url);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    fireEvent.click(
      await screen.findByRole("button", { name: "Set up Engine + Pixi" }),
    );

    await waitFor(() => {
      const request = fetchMock.mock.calls.find(
        ([, init]) => init?.method === "POST",
      );
      expect(request).toBeDefined();
      expect(JSON.parse(String(request?.[1]?.body))).toEqual({
        kind: "dependency.stack.prepare",
      });
    });
  });

  test("shows live progress and logs while compatible sources are syncing", async () => {
    const runningStackJob: Job = {
      ...stackJob,
      status: "running",
      started_at: "2026-08-29T00:00:01Z",
      logs: ["Fetching the reviewed Engine revision…"],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/v1/jobs")) return response([runningStackJob]);
        return readResponse(url);
      }),
    );

    render(<App />);

    const loader = await screen.findByRole("status", {
      name: "Syncing compatible Engine and Pixi sources",
    });
    expect(within(loader).getByText("Fetching the reviewed Engine revision…")).toBeInTheDocument();
    expect(within(loader).getByText("Sources").closest("li")).toHaveClass("active");
  });

  test("identifies a standalone Pixi build as the active slow operation", async () => {
    const syncedStatus: CapabilityStatus = {
      ...status,
      engine: { ...status.engine, source_available: true, revision: "engine-pinned", synced: true },
      pixi: { ...status.pixi, source_available: true, revision: "pixi-pinned", synced: true },
    };
    const pixiJob: Job = {
      ...stackJob,
      id: "pixi-job",
      kind: "dependency.pixi.prepare",
      label: "Prepare DeepDeckPixi",
      status: "running",
      started_at: "2026-08-29T00:00:01Z",
      logs: ["Building the production Pixi bundle…"],
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/api/v1/status")) return response(syncedStatus);
        if (url.endsWith("/api/v1/jobs")) return response([pixiJob]);
        return readResponse(url, syncedStatus);
      }),
    );

    render(<App />);

    const loader = await screen.findByRole("status", {
      name: "Building the Pixi visual client",
    });
    expect(within(loader).getByText("Building the production Pixi bundle…")).toBeInTheDocument();
    expect(within(loader).getByText("Pixi").closest("li")).toHaveClass("active");
  });

  test("a ready local stack can still be verified and repaired", async () => {
    const readyStatus: CapabilityStatus = {
      ...status,
      engine: {
        ...status.engine,
        source_available: true,
        revision: "engine-pinned",
        synced: true,
        built: true,
        healthy: true,
      },
      pixi: {
        ...status.pixi,
        source_available: true,
        revision: "pixi-pinned",
        built_revision: "pixi-pinned",
        synced: true,
        built: true,
        build_present: true,
      },
      hosted: { ...status.hosted, api_key_configured: true },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/status")) return response(readyStatus);
      if (url.endsWith("/api/v1/session")) return response({ token: "local-token" });
      if (url.endsWith("/api/v1/jobs") && init?.method === "POST") return response(stackJob);
      return readResponse(url, readyStatus);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    const setupToggle = await screen.findByRole("button", { name: /Application setup/i });
    await waitFor(() => expect(setupToggle).toHaveAttribute("aria-expanded", "false"));
    fireEvent.click(setupToggle);
    await waitFor(() => expect(setupToggle).toHaveAttribute("aria-expanded", "true"));
    expect(await screen.findByText("Account connected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Verify & repair Engine + Pixi" }));

    await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  });

  test("shows every agent in one allocation table and saves a row", async () => {
    const models = [
      {
        id: "agent-one",
        name: "Agent One",
        architecture: "v12",
        format: "legacy",
        description: "Legacy agent",
        createdAt: "2026-08-30T00:00:00Z",
        runPath: "run-one",
        checkpointPath: "checkpoint-one",
        status: "running",
        ready: true,
        reservePlaytest: true,
        decks: [],
      },
      {
        id: "agent-two",
        name: "Agent Two",
        architecture: "v11",
        format: "commander",
        description: "Commander agent",
        createdAt: "2026-08-29T00:00:00Z",
        runPath: "run-two",
        checkpointPath: "checkpoint-two",
        status: "stopped",
        ready: false,
        reservePlaytest: true,
        decks: [],
      },
    ];
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      void init;
      const url = String(input);
      if (url.endsWith("/api/v1/models")) return response({ items: models });
      if (url.includes("/api/v1/models/") && url.endsWith("/resources")) {
        return response({ trainingMatches: 2, leagueMatches: 1, localMatches: 1, gpuMemoryMb: 4096 });
      }
      if (url.endsWith("/api/v1/session")) return response({ token: "local-token" });
      return readResponse(url);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /Jobs/ }));
    const table = await screen.findByRole("table", { name: "Agent resource allocation" });
    expect(within(table).getByRole("row", { name: /Agent One/i })).toBeInTheDocument();
    expect(within(table).getByRole("row", { name: /Agent Two/i })).toBeInTheDocument();
    const trainingSlots = await screen.findByLabelText("Self-play slots for Agent One");
    const firstRow = trainingSlots.closest("tr");
    expect(firstRow).not.toBeNull();
    fireEvent.change(trainingSlots, {
      target: { value: "3" },
    });
    fireEvent.click(within(firstRow as HTMLTableRowElement).getByRole("button", { name: "Save allocation" }));

    await waitFor(() => {
      const save = fetchMock.mock.calls.find(([input, init]) =>
        String(input).endsWith("/api/v1/models/agent-one/resources") && init?.method === "PUT",
      );
      expect(save).toBeDefined();
      expect(JSON.parse(String(save?.[1]?.body))).toMatchObject({ trainingMatches: 3 });
    });
  });

  test("downloads selected training decks without substituting smoke data", async () => {
    const trainingStatus: CapabilityStatus = {
      ...status,
      engine: {
        ...status.engine,
        source_available: true,
        synced: true,
        built: true,
        healthy: true,
      },
      hosted: {
        api_key_configured: true,
        trajectory_training: false,
        reason: "Not available yet.",
      },
      workflows: { training_decks: false },
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/api/v1/status")) return response(trainingStatus);
      if (url.endsWith("/api/v1/session")) return response({ token: "local-token" });
      if (url.endsWith("/api/v1/jobs") && init?.method === "POST") return response(stackJob);
      if (url.includes("/api/v1/catalog/decks/") && init?.method === "POST") return response({ versionId: "deck-1", name: "Legacy Reanimator", format: "legacy", cardCount: 60, path: "deck.json" });
      if (url.endsWith("/api/v1/training/deck-pool") && init?.method === "PUT") return response({ decks: [] });
      if (url.includes("/api/v1/catalog/decks") && url.includes("format=commander")) return response({ items: [] });
      if (url.includes("/api/v1/catalog/decks"))
        return response({
          items: [
            {
              id: "deck-1",
              name: "Legacy Reanimator",
              version: 1,
              format: "legacy",
              colors: ["B"],
              playableCardCount: 60,
            },
            {
              id: "deck-2",
              name: "Death and Taxes",
              version: 1,
              format: "legacy",
              colors: ["W"],
              playableCardCount: 60,
            },
          ],
        });
      return readResponse(url, trainingStatus);
    });
    vi.stubGlobal("fetch", fetchMock);
    render(<App />);

    fireEvent.click(await screen.findByRole("button", { name: /New agent/i }));

    fireEvent.click(
      await screen.findByRole("button", { name: /Legacy Reanimator/i }),
    );
    await waitFor(() => {
      const request = fetchMock.mock.calls.find((call) => {
        const [input, init] = call as unknown as [
          RequestInfo | URL,
          RequestInit?,
        ];
        return String(input).includes("/api/v1/catalog/decks/deck-1/download") && init?.method === "POST";
      });
      expect(request).toBeDefined();
      expect(fetchMock.mock.calls.some((call) => {
        const [input, init] = call as unknown as [RequestInfo | URL, RequestInit?];
        return String(input).endsWith("/api/v1/jobs")
          && init?.method === "POST"
          && JSON.parse(String(init.body)).kind === "training.smoke";
      })).toBe(false);
    });
  });
});
