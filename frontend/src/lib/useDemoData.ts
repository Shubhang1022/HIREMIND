'use client';

import { useEffect, useState } from 'react';
import { mockCandidates, type Candidate } from '@/lib/mockData';

const DEMO_LOAD_MS = 350;

export function useDemoCandidates() {
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      if (cancelled) return;
      try {
        setCandidates(mockCandidates);
        setError(null);
      } catch {
        setError('Failed to load candidate data. Please refresh.');
      } finally {
        setLoading(false);
      }
    }, DEMO_LOAD_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, []);

  const retry = () => {
    setLoading(true);
    setError(null);
    window.setTimeout(() => {
      setCandidates(mockCandidates);
      setLoading(false);
    }, DEMO_LOAD_MS);
  };

  return { candidates, loading, error, retry };
}
