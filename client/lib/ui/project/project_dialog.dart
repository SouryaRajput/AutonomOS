import 'package:flutter/material.dart';
import '../../core/tokens/tokens.dart';
import '../../models/project.dart';
import '../../state/app_state.dart';

class ProjectDialog extends StatefulWidget {
  final AppState appState;

  const ProjectDialog({super.key, required this.appState});

  @override
  State<ProjectDialog> createState() => _ProjectDialogState();
}

class _ProjectDialogState extends State<ProjectDialog> {
  final _nameController = TextEditingController();
  final _pathController = TextEditingController();
  final _descController = TextEditingController();
  bool _isCreating = false;

  @override
  void dispose() {
    _nameController.dispose();
    _pathController.dispose();
    _descController.dispose();
    super.dispose();
  }

  void _handleCreate() async {
    final name = _nameController.text.trim();
    final path = _pathController.text.trim();
    if (name.isEmpty || path.isEmpty) return;

    setState(() {
      _isCreating = true;
    });

    await widget.appState.createProject(
      name: name,
      rootPath: path,
      description: _descController.text.trim(),
    );

    if (mounted) {
      Navigator.of(context).pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final projects = widget.appState.projects;

    return Dialog(
      shape: RoundedRectangleBorder(borderRadius: AppTokens.borderRadiusLg),
      child: Container(
        width: 540,
        padding: const EdgeInsets.all(AppTokens.space24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Text('Projects & Workspaces', style: theme.textTheme.headlineMedium),
                const Spacer(),
                IconButton(
                  icon: const Icon(Icons.close, size: 20),
                  onPressed: () => Navigator.of(context).pop(),
                ),
              ],
            ),
            const SizedBox(height: AppTokens.space16),

            // Existing Projects List
            if (projects.isNotEmpty) ...[
              Text('Switch Project', style: theme.textTheme.titleMedium),
              const SizedBox(height: AppTokens.space8),
              Container(
                constraints: const BoxConstraints(maxHeight: 180),
                decoration: BoxDecoration(
                  borderRadius: AppTokens.borderRadiusMd,
                  border: Border.all(color: Theme.of(context).dividerColor),
                ),
                child: ListView.separated(
                  shrinkWrap: true,
                  itemCount: projects.length,
                  separatorBuilder: (_, __) => const Divider(height: 1),
                  itemBuilder: (context, idx) {
                    final p = projects[idx];
                    final isSelected = widget.appState.selectedProject?.id == p.id;
                    return ListTile(
                      dense: true,
                      selected: isSelected,
                      title: Text(p.name, style: const TextStyle(fontWeight: FontWeight.w600)),
                      subtitle: Text(p.rootPath, style: const TextStyle(fontSize: 11)),
                      trailing: isSelected ? const Icon(Icons.check, size: 16, color: AppTokens.brandPrimaryLight) : null,
                      onTap: () {
                        widget.appState.selectProject(p);
                        Navigator.of(context).pop();
                      },
                    );
                  },
                ),
              ),
              const SizedBox(height: AppTokens.space20),
            ],

            // New Project Form
            Text('Create New Project', style: theme.textTheme.titleMedium),
            const SizedBox(height: AppTokens.space12),
            TextField(
              controller: _nameController,
              decoration: const InputDecoration(labelText: 'Project Name', hintText: 'e.g. Auth Microservice'),
            ),
            const SizedBox(height: AppTokens.space12),
            TextField(
              controller: _pathController,
              decoration: const InputDecoration(labelText: 'Root Workspace Path', hintText: '/Users/me/dev/my-project'),
            ),
            const SizedBox(height: AppTokens.space12),
            TextField(
              controller: _descController,
              decoration: const InputDecoration(labelText: 'Description (Optional)', hintText: 'Brief summary of the codebase'),
            ),
            const SizedBox(height: AppTokens.space24),
            Row(
              mainAxisAlignment: MainAxisAlignment.end,
              children: [
                TextButton(
                  onPressed: () => Navigator.of(context).pop(),
                  child: const Text('Cancel'),
                ),
                const SizedBox(width: AppTokens.space12),
                ElevatedButton(
                  onPressed: _isCreating ? null : _handleCreate,
                  style: ElevatedButton.styleFrom(
                    backgroundColor: AppTokens.brandPrimary,
                    foregroundColor: Colors.white,
                  ),
                  child: _isCreating
                      ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2, color: Colors.white))
                      : const Text('Create Project'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
