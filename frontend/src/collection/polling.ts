import type { CollectionRequest, CollectionRequestStatus } from "../api/types";

export const POLL_TIMEOUT_MS = 120_000;

const pollDelays = [2_000, 4_000, 8_000, 10_000] as const;

export type CollectionUiStatus =
  | "idle"
  | "submitting"
  | CollectionRequestStatus
  | "timed_out";

export type CollectionUiState = {
  status: CollectionUiStatus;
  request: CollectionRequest | null;
  errorCode: string | null;
};

export type CollectionSession = {
  request: CollectionRequest | null;
  submission: Promise<CollectionRequest> | null;
};

export type CollectionAction =
  | { type: "submitting" }
  | { type: "received"; request: CollectionRequest }
  | { type: "timed_out"; request: CollectionRequest }
  | { type: "request_failed"; errorCode: string | null }
  | { type: "reset" };

export const initialCollectionState: CollectionUiState = {
  status: "idle",
  request: null,
  errorCode: null,
};

export function collectionReducer(
  _state: CollectionUiState,
  action: CollectionAction,
): CollectionUiState {
  switch (action.type) {
    case "submitting":
      return { status: "submitting", request: null, errorCode: null };
    case "received":
      return { status: action.request.status, request: action.request, errorCode: action.request.error_code };
    case "timed_out":
      return { status: "timed_out", request: action.request, errorCode: null };
    case "request_failed":
      return { status: "failed", request: null, errorCode: action.errorCode };
    case "reset":
      return initialCollectionState;
  }
}

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
