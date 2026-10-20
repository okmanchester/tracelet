import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Alert, Card, Row, Col, Menu, Tag, Spin, Typography } from "antd";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { listDocs, getDoc } from "../api";

export default function Docs() {
  const [params, setParams] = useSearchParams();
  const requestedVersion = params.get("knowledge_version") || undefined;
  const requestedDoc = params.get("doc_id");
  const requestedChunk = params.get("chunk_id");
  const [docs, setDocs] = useState<any[]>([]);
  const [current, setCurrent] = useState<any>(null);
  const [version, setVersion] = useState<string>();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    let disposed = false;
    setLoading(true);
    setError("");
    setCurrent(null);
    (async () => {
      try {
        const listing = await listDocs(requestedVersion);
        if (disposed) return;
        setDocs(listing.documents);
        setVersion(listing.knowledge_version);
        const id = requestedDoc || listing.documents[0]?.id;
        const detail = id ? await getDoc(id, listing.knowledge_version) : null;
        if (!disposed) setCurrent(detail);
      } catch {
        if (!disposed) { setDocs([]); setError("无法读取指定知识版本，请稍后重试。"); }
      } finally {
        if (!disposed) setLoading(false);
      }
    })();
    return () => { disposed = true; };
  }, [requestedVersion, requestedDoc]);
  useEffect(() => {
    if (current && requestedChunk) document.getElementById(`chunk-${requestedChunk}`)?.scrollIntoView();
  }, [current, requestedChunk]);
  return <Row gutter={12} style={{ maxWidth: 1200, margin: "0 auto" }}>
    <Col span={6}><Card title="文档中心" size="small">
      {version && <Tag>版本 {version.slice(0, 8)}</Tag>}
      <Menu selectedKeys={current ? [current.id] : []}
        onClick={(e) => setParams({ doc_id: e.key, knowledge_version: version || "" })}
        items={docs.map((d) => ({ key: d.id, label: <span>{d.title} <Tag>{d.category}</Tag></span> }))} />
    </Card></Col>
    <Col span={18}><Card size="small" style={{ minHeight: "70vh" }}>
      {error ? <Alert type="error" message={error} /> : loading ? <Spin /> : current ? <>
        <Typography.Title level={4}>{current.title}</Typography.Title>
        <Tag color="blue">{current.category}</Tag>
        {requestedChunk && current.chunks?.filter((c: any) => c.chunk_id === requestedChunk).map((c: any) =>
          <Card key={c.chunk_id} id={`chunk-${c.chunk_id}`} title={`引用：${c.section}`} size="small">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{c.content}</ReactMarkdown>
          </Card>)}
        <div className="md" style={{ marginTop: 16 }}><ReactMarkdown remarkPlugins={[remarkGfm]}>{current.content}</ReactMarkdown></div>
      </> : <Typography.Text type="secondary">暂无已发布文档</Typography.Text>}
    </Card></Col>
  </Row>;
}
