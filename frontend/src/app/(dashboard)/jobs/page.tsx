import { redirect } from 'next/navigation';

// Jobs are now managed within each Project — redirect to Projects
export default function JobsPage() {
  redirect('/projects');
}
