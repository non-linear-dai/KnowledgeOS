# frozen_string_literal: true

require "minitest/autorun"
require "tmpdir"
require "fileutils"

$LOAD_PATH.unshift(File.expand_path("../lib", __dir__))
require "knowledge_os"

module WorkspaceHelper
  def with_workspace
    Dir.mktmpdir("knowledgeos-test") do |dir|
      %w[control knowledge connectors].each do |name|
        FileUtils.cp_r(File.expand_path("../#{name}", __dir__), File.join(dir, name))
      end
      yield KnowledgeOS::Config.new(dir)
    end
  end

  def compiled_service(config)
    compiler = KnowledgeOS::Compiler.new(config: config)
    compiler.compile(rebuild: true)
    compiler.close
    KnowledgeOS::Service.new(config: config)
  end
end

