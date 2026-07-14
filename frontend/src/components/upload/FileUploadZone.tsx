'use client';

import { useCallback, useState } from 'react';
import { Upload, File, X, Loader2, FolderOpen } from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { toast } from 'sonner';

const ACCEPTED = '.csv,.xlsx,.xls,.json,.jsonl,.txt,.pdf,.docx,.doc';

interface FileUploadZoneProps {
  onUpload: (files: File[]) => Promise<void>;
  uploadType?: 'candidates' | 'job_description';
  multiple?: boolean;
  className?: string;
}

export function FileUploadZone({ onUpload, uploadType = 'candidates', multiple = true, className }: FileUploadZoneProps) {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [files, setFiles] = useState<File[]>([]);

  const handleFiles = useCallback(async (fileList: FileList | File[]) => {
    const arr = Array.from(fileList);
    if (!arr.length) return;
    setFiles(prev => [...prev, ...arr]);
    setUploading(true);
    try {
      await onUpload(arr);
      if (uploadType !== 'candidates') {
        toast.success(`Uploaded ${arr.length} file(s) successfully`);
      }
      setFiles([]);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Upload failed');
    } finally {
      setUploading(false);
    }
  }, [onUpload]);

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    handleFiles(e.dataTransfer.files);
  };

  return (
    <div className={cn('space-y-4', className)}>
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          'relative border-2 border-dashed rounded-xl p-12 text-center transition-all duration-200',
          dragging ? 'border-indigo-500 bg-indigo-500/5' : 'border-border hover:border-indigo-500/50',
          uploading && 'opacity-50 pointer-events-none'
        )}
      >
        {uploading ? (
          <Loader2 className="w-10 h-10 mx-auto mb-4 animate-spin text-indigo-400" />
        ) : (
          <Upload className="w-10 h-10 mx-auto mb-4 text-muted-foreground" />
        )}
        <p className="font-medium mb-1">
          {uploadType === 'candidates' ? 'Drop candidate files or folders here' : 'Drop job description file here'}
        </p>
        <p className="text-sm text-muted-foreground mb-4">
          Supports CSV, XLSX, JSON, TXT, PDF, DOCX
        </p>
        <div className="flex gap-3 justify-center">
          <label>
            <input type="file" accept={ACCEPTED} multiple={multiple} className="hidden"
              onChange={e => e.target.files && handleFiles(e.target.files)} />
            <Button variant="outline" nativeButton={false} render={<span><File className="w-4 h-4 mr-2" />Choose Files</span>} />
          </label>
          {uploadType === 'candidates' && (
            <label>
              <input type="file" {...{ webkitdirectory: '', directory: '' } as React.InputHTMLAttributes<HTMLInputElement>} multiple className="hidden"
                onChange={e => e.target.files && handleFiles(e.target.files)} />
              <Button variant="outline" nativeButton={false} render={<span><FolderOpen className="w-4 h-4 mr-2" />Upload Folder</span>} />
            </label>
          )}
        </div>
      </div>

      {files.length > 0 && (
        <div className="space-y-2">
          {files.map((f, i) => (
            <div key={i} className="flex items-center gap-3 p-3 rounded-lg bg-muted/50">
              <File className="w-4 h-4 text-muted-foreground" />
              <span className="text-sm flex-1 truncate">{f.name}</span>
              <span className="text-xs text-muted-foreground">{(f.size / 1024).toFixed(1)} KB</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
