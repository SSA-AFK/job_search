export type CollectionState = "loading" | "unavailable" | "error";

export function CollectionStatus({ state }: { state: CollectionState }) {
  const message = state === "loading"
    ? "正在确认是否可以补充资料"
    : state === "unavailable"
      ? "采集服务暂不可用，请稍后再试"
      : "资料补充请求未能提交，请稍后再试";

  return (
    <div className="collection-status" role="status" aria-live="polite" aria-atomic="true">
      <h2>暂未收录这家公司</h2>
      <p>{message}</p>
    </div>
  );
}
