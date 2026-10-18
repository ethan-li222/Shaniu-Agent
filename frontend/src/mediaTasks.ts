export type MediaArtifact = { kind: string; filename: string; url: string }
export type PostJob = { job_id: string; operation: string; status: string; progress: number; stage?: string; failure_reason?: string; artifacts: { filename: string; url: string }[] }
export type MediaTask = {
  task_id: string; message_id: string; turn_id: string; kind: string; subject: string
  status: string; progress: number; stage?: string; failure_reason?: string
  connection_error?: string; artifacts: MediaArtifact[]
  speech_provider?: string; speech_warning?: string
  postprocess?: PostJob[]
  enhancement?: { job_id: string; status: string; progress: number; stage?: string; failure_reason?: string; video_url?: string; project_url?: string; report_url?: string }
}
