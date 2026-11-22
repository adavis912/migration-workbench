CREATE OR REPLACE PACKAGE recruit_pkg AS
  PROCEDURE advance_stage(p_candidate_id IN NUMBER, p_stage OUT VARCHAR2);
END recruit_pkg;
/
CREATE OR REPLACE PACKAGE BODY recruit_pkg AS
  PROCEDURE advance_stage(p_candidate_id IN NUMBER, p_stage OUT VARCHAR2) IS
  BEGIN
    UPDATE candidates
       SET stage = DECODE(stage, 'APPLIED', 'SCREEN', 'SCREEN', 'INTERVIEW', 'INTERVIEW', 'OFFER', stage),
           updated_at = SYSDATE
     WHERE candidate_id = p_candidate_id
    RETURNING stage INTO p_stage;
    COMMIT;
  END advance_stage;
END recruit_pkg;
/
