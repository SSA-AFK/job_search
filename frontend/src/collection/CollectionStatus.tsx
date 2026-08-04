import { RotateCw } from "lucide-react";
import { useEffect, useReducer, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api, ApiError } from "../api/client";
import type { CollectionRequest } from "../api/types";
import {
  collectionReducer,
  CollectionCapacityError,
  type CollectionRegistry,
  type CollectionSession,
  defaultCollectionRegistry,
  initialCollectionState,
  nextPollDelay,
  publicCollectionError,
  shouldPoll,
} from "./polling";

function waitForPoll(delay: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timeout = window.setTimeout(resolve, delay);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timeout);
      reject(new DOMException("Collection polling was aborted", "AbortError"));
    }, { once: true });
  });
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === "AbortError";
}

export function CollectionStatus({
  query,
  registry = defaultCollectionRegistry,
}: {
  query: string;
  registry?: CollectionRegistry;
}) {
  const navigate = useNavigate();
  const [state, dispatch] = useReducer(collectionReducer, initialCollectionState);
  const [refreshing, setRefreshing] = useState(false);
  const sessionRef = useRef<CollectionSession | null>(null);
  const manualControllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    let session: CollectionSession;
    try {
      session = registry.getOrCreate(query);
    } catch (error: unknown) {
      dispatch({
        type: "submission_failed",
        errorCode: error instanceof CollectionCapacityError ? "collection_unavailable" : null,
      });
      return;
    }
    const controller = new AbortController();
    sessionRef.current = session;
    let active = true;

    const update = (request: CollectionRequest) => {
      registry.rememberRequest(session, request);
      if (!active || session.expired) return;
      dispatch({ type: "received", request });
      if (request.status === "succeeded" && request.company_id) {
        navigate(`/companies/${request.company_id}`);
      }
    };

    const stopAtDeadline = () => {
      controller.abort();
      if (active && session.expired) dispatch({ type: "timed_out", request: session.request });
    };

    const unsubscribe = registry.subscribe(session, () => {
      if (session.expired) {
        stopAtDeadline();
      } else if (session.submissionError && !session.request && active) {
        const errorCode = session.submissionError instanceof ApiError ? session.submissionError.code ?? null : null;
        dispatch({ type: "submission_failed", errorCode });
      }
    });

    const collect = async () => {
      if (session.expired) {
        if (session.request && !shouldPoll(session.request.status)) {
          dispatch({ type: "received", request: session.request });
          if (session.request.status === "succeeded" && session.request.company_id) {
            navigate(`/companies/${session.request.company_id}`);
          }
          return;
        }
        stopAtDeadline();
        return;
      }

      dispatch({ type: "submitting" });
      let request = session.request;
      if (!request) {
        if (session.submissionError) throw session.submissionError;
        if (!session.submission) return;
        request = await session.submission;
        if (!active || session.expired) return;
      }

      update(request);
      if (!shouldPoll(request.status)) return;

      let attempt = 0;
      while (shouldPoll(request.status) && !session.expired) {
        const remaining = session.deadlineAt - Date.now();
        if (remaining <= 0) {
          registry.expire(session);
          return;
        }
        await waitForPoll(Math.min(nextPollDelay(attempt), remaining), controller.signal);
        if (!active || session.expired || session.deadlineAt - Date.now() <= 0) {
          if (!session.expired) registry.expire(session);
          return;
        }
        request = await api.getCollectionRequest(request.id, controller.signal);
        if (!active || session.expired) return;
        update(request);
        attempt += 1;
      }
    };

    void collect().catch((error: unknown) => {
      if (!active || isAbortError(error) || session.expired) return;
      if (session.request) {
        dispatch({ type: "transport_failed", request: session.request });
      } else {
        const errorCode = error instanceof ApiError ? error.code ?? null : null;
        dispatch({ type: "submission_failed", errorCode });
      }
    });

    return () => {
      active = false;
      unsubscribe();
      controller.abort();
      manualControllerRef.current?.abort();
    };
  }, [navigate, query, registry]);

  const refreshStatus = () => {
    const session = sessionRef.current;
    const request = session?.request;
    if (!session || !request || refreshing) return;

    const controller = new AbortController();
    manualControllerRef.current?.abort();
    manualControllerRef.current = controller;
    setRefreshing(true);

    void api.getCollectionRequest(request.id, controller.signal)
      .then((updatedRequest) => {
        if (controller.signal.aborted) return;
        registry.rememberManualRequest(session, updatedRequest);
        if (updatedRequest.status === "succeeded" && updatedRequest.company_id) {
          dispatch({ type: "received", request: updatedRequest });
          navigate(`/companies/${updatedRequest.company_id}`);
          return;
        }
        if (shouldPoll(updatedRequest.status)) {
          dispatch({ type: "timed_out", request: updatedRequest });
          return;
        }
        dispatch({ type: "received", request: updatedRequest });
      })
      .catch((error: unknown) => {
        if (!isAbortError(error)) dispatch({ type: "transport_failed", request });
      })
      .finally(() => {
        if (manualControllerRef.current === controller) setRefreshing(false);
      });
  };

  const content = state.status === "submitting"
    ? ["正在提交采集请求", "正在确认是否可以补充资料"]
    : state.status === "queued"
      ? ["正在排队", "已安排资料采集，完成后会自动更新结果。"]
      : state.status === "running"
        ? ["正在采集", "正在整理公开资料，完成后会自动更新结果。"]
        : state.status === "partial"
          ? ["已完成部分资料采集", "部分公开资料已更新，可以稍后再次搜索。"]
          : state.status === "succeeded"
            ? ["采集完成", "正在打开公司资料。"]
            : state.status === "timed_out"
              ? ["采集仍在进行中", "状态自动更新已暂停，请按需刷新查看最新结果。"]
              : state.status === "failed" || state.status === "submission_failed"
                ? ["采集未完成", publicCollectionError(state.errorCode)]
                : ["暂未收录这家公司", "正在确认是否可以补充资料"];

  const canRefresh = state.status === "timed_out" || state.transportError;

  return (
    <div className="collection-status" role="status" aria-live="polite" aria-atomic="true">
      <div>
        <h2>{content[0]}</h2>
        <p>{content[1]}</p>
        {state.transportError ? <p>状态暂时无法更新，请稍后刷新。</p> : null}
      </div>
      {canRefresh ? (
        <button className="secondary-button" type="button" onClick={refreshStatus} disabled={refreshing || !state.request}>
          <RotateCw aria-hidden="true" size={16} />
          刷新状态
        </button>
      ) : null}
    </div>
  );
}
