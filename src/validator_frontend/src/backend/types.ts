export interface ValidatorSession {
  sessionId: string;
  validatorType: 'first' | 'second';
  startedAt: number;
  batchType: 'easy' | 'hard';
  sessionCompletedCount: number;
  
  claimedImageIds: number[];
  claimedAt: number;
}

export interface ImageForValidation {
  id: number;
  problemId: number;
  link: string;
  description: string;
  cerinta: string;
  explicatie: string;
}

export interface ValidationResult {
  imageId: number;
  approved: boolean;
  modifications: string | null;
}

export interface ValidatorStats {
  claimed: number;
  completed: number;
  totalRemaining: number;
}

export type BatchType = 'easy' | 'hard';
export type ValidatorType = 'first' | 'second';

// Batch assignment based on validator type and difficulty
export interface BatchAssignment {
  sessionId: string;
  imageIds: number[];     // IDs of images assigned to this session
  totalRemaining: number;// Total remaining for this validator type/difficulty
}
