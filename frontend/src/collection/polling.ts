import { api } from "../api/client";
import type { CollectionRequest, CollectionRequestStatus } from "../api/types";

export const POLL_TIMEOUT_MS = 120_000;

const pollDelays = [2_000, 4_000, 8_000, 10_000] as const;
const SESSION_TTL_MS = 30 * 60_000;
const MAX_SESSIONS = 50;

export type CollectionUiStatus =
  | "idle"
  | "submitting"
  | "submission_failed"
  | CollectionRequestStatus
  | "timed_out";

export type CollectionUiState = {
  status: CollectionUiStatus;
  request: CollectionRequest | null;
  errorCode: string | null;
  transportError: boolean;
};

export type CollectionSession = {
  readonly key: string;
  readonly query: string;
  request: CollectionRequest | null;
  submission: Promise<CollectionRequest> | null;
  submissionError: unknown | null;
  expired: boolean;
  deadlineAt: number;
  lastAccessedAt: number;
  readonly submissionController: AbortController;
  deadlineTimer: ReturnType<typeof setTimeout> | null;
  readonly listeners: Set<() => void>;
};

export type CollectionRegistry = {
  getOrCreate(query: string): CollectionSession;
  subscribe(session: CollectionSession, listener: () => void): () => void;
  rememberRequest(session: CollectionSession, request: CollectionRequest): void;
  expire(session: CollectionSession): void;
};

export type CollectionAction =
  | { type: "submitting" }
  | { type: "received"; request: CollectionRequest }
  | { type: "timed_out"; request: CollectionRequest | null }
  | { type: "submission_failed"; errorCode: string | null }
  | { type: "transport_failed"; request: CollectionRequest | null }
  | { type: "reset" };

export const initialCollectionState: CollectionUiState = {
  status: "idle",
  request: null,
  errorCode: null,
  transportError: false,
};

export function collectionReducer(
  state: CollectionUiState,
  action: CollectionAction,
): CollectionUiState {
  switch (action.type) {
    case "submitting":
      return { status: "submitting", request: null, errorCode: null, transportError: false };
    case "received":
      return {
        status: action.request.status,
        request: action.request,
        errorCode: action.request.error_code,
        transportError: false,
      };
    case "timed_out":
      return { status: "timed_out", request: action.request, errorCode: null, transportError: false };
    case "submission_failed":
      return { status: "submission_failed", request: null, errorCode: action.errorCode, transportError: false };
    case "transport_failed":
      return { ...state, request: action.request ?? state.request, transportError: true };
    case "reset":
      return initialCollectionState;
  }
}

export function normalizeCollectionQuery(query: string) {
  return query.normalize("NFKC").toLocaleLowerCase("und").replace(/\s+/gu, "");
}

function isTerminal(request: CollectionRequest | null) {
  return request !== null && !shouldPoll(request.status);
}

function isInactive(session: CollectionSession) {
  return session.expired || isTerminal(session.request) || session.submissionError !== null;
}

function notify(session: CollectionSession) {
  for (const listener of session.listeners) listener();
}

export function createCollectionRegistry(): CollectionRegistry {
  const sessions = new Map<string, CollectionSession>();

  const evictExpiredSessions = () => {
    const now = Date.now();
    for (const [key, session] of sessions) {
      if (isInactive(session) && session.listeners.size === 0 && now - session.lastAccessedAt > SESSION_TTL_MS) {
        sessions.delete(key);
      }
    }
    if (sessions.size <= MAX_SESSIONS) return;
    const evictable = [...sessions.values()]
      .filter((session) => isInactive(session) && session.listeners.size === 0)
      .sort((left, right) => left.lastAccessedAt - right.lastAccessedAt);
    for (const session of evictable) {
      if (sessions.size <= MAX_SESSIONS) break;
      sessions.delete(session.key);
    }
  };

  const rememberRequest = (session: CollectionSession, request: CollectionRequest) => {
    if (session.expired) return;
    session.request = request;
    session.lastAccessedAt = Date.now();
    if (isTerminal(request) && session.deadlineTimer !== null) {
      clearTimeout(session.deadlineTimer);
      session.deadlineTimer = null;
    }
    notify(session);
  };

  const expire = (session: CollectionSession) => {
    if (session.expired) return;
    session.expired = true;
    if (session.deadlineTimer !== null) {
      clearTimeout(session.deadlineTimer);
      session.deadlineTimer = null;
    }
    session.submissionController.abort();
    notify(session);
  };

  return {
    getOrCreate(query) {
      const key = normalizeCollectionQuery(query);
      const existing = sessions.get(key);
      if (existing) {
        existing.lastAccessedAt = Date.now();
        return existing;
      }

      evictExpiredSessions();
      const submissionController = new AbortController();
      const session: CollectionSession = {
        key,
        query,
        request: null,
        submission: null,
        submissionError: null,
        expired: false,
        deadlineAt: Date.now() + POLL_TIMEOUT_MS,
        lastAccessedAt: Date.now(),
        submissionController,
        deadlineTimer: null,
        listeners: new Set(),
      };
      sessions.set(key, session);

      session.deadlineTimer = setTimeout(() => {
        expire(session);
      }, POLL_TIMEOUT_MS);

      session.submission = api.createCollectionRequest(query, submissionController.signal);
      void session.submission.then(
        (request) => {
          session.submission = null;
          rememberRequest(session, request);
        },
        (error: unknown) => {
          session.submission = null;
          session.submissionError = error;
          if (session.deadlineTimer !== null) {
            clearTimeout(session.deadlineTimer);
            session.deadlineTimer = null;
          }
          notify(session);
        },
      );
      // A view can unmount while the request is pending; preserve the durable result/error without an unhandled rejection.
      void session.submission.catch(() => undefined);
      return session;
    },
    subscribe(session, listener) {
      session.listeners.add(listener);
      return () => session.listeners.delete(listener);
    },
    rememberRequest,
    expire,
  };
}

export const defaultCollectionRegistry = createCollectionRegistry();

export function nextPollDelay(attempt: number) {
  return pollDelays[Math.min(attempt, pollDelays.length - 1)];
}

export function shouldPoll(status: CollectionRequestStatus) {
  return status === "queued" || status === "running";
}

export function publicCollectionError(errorCode: string | null) {
  if (errorCode === "collection_unavailable") return "采集服务暂不可用，请稍后再试";
  return "资料采集未能完成，请稍后再试";
}
