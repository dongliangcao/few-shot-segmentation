"""Evaluation Script"""
import tqdm
import numpy as np
import torch
import torch.optim
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
from torchvision.transforms import Compose

from models.fewshot import FewShotSegNet
from models.metric import MetricSegNet
from dataset.voc import voc_fewshot
from dataset.transforms import ToTensorNormalize, Resize
from util.metric import Metric
from util.utils import set_seed, CLASS_LABELS, knn_predict
import config

def test():
    # reproducibility
    set_seed(config.seed)
    # sanity check
    assert config.model_type in ['fewshot', 'metric'], f'Unknown mode type: {config.model_type}, expect [fewshot, metric]'
    # create model
    print('##### Create Model #####')
    if config.model_type == 'fewshot':
        model = FewShotSegNet(pretrained_path=config.path['init_path']).cuda()
    else:
        model = MetricSegNet(pretrained_path=config.path['init_path']).cuda()
    if not config.notrain:
        model.load_state_dict(torch.load(config.snapshot))
    model.eval()

    # prepare data
    print('##### Prepare data #####')

    labels = CLASS_LABELS['VOC']['all']
    transforms = Compose([
        Resize(size=config.input_size)
    ])

    print('##### Testing Begins #####')
    metric = Metric(max_label=20, n_runs=config.n_runs)
    with torch.no_grad():
        for run in range(config.n_runs):
            print(f'### Run {run + 1} ###')
            set_seed(config.seed + run)

            print(f'### Load data ###')
            assert config.task['n_ways'] == 1 and config.task['n_shots'] == 1 and config.task['n_queries'] == 1, 'currently, the code only supports n_ways = 1, n_shots = 1 and n_queries = 1'
            dataset = voc_fewshot(
                base_dir=config.path['data_dir'],
                split=config.path['data_split'],
                transforms=transforms,
                to_tensor=ToTensorNormalize(),
                labels=labels,
                max_iters=config.n_steps*config.batch_size,
                n_ways=config.task['n_ways'],
                n_shots=config.task['n_shots'],
                n_queries=config.task['n_queries']
            )
            testloader = DataLoader(dataset, batch_size=config.batch_size, shuffle=False, num_workers=1, pin_memory=True)

            print(f'Total num of Data: {len(dataset)}')

            for sample_batch in tqdm.tqdm(testloader):
                label_ids = list(sample_batch['class_ids']) # ways
                support_images = [[shot.cuda() for shot in way] for way in sample_batch['support_images']] # ways x shots x [B, 3, H, W]
                
                query_images = [query_image.cuda()
                                for query_image in sample_batch['query_images']] # queries x [B, 3, H, W]
                query_labels = torch.cat(
                    [query_label.cuda()for query_label in sample_batch['query_labels']], dim=0) # [B*queries, H, W]

                support_fg_mask = [[shot['fg_mask'].float().cuda() for shot in way]
                                       for way in sample_batch['support_mask']]
                support_bg_mask = [[shot['bg_mask'].float().cuda() for shot in way]
                                       for way in sample_batch['support_mask']]
                
                if config.model_type == 'fewshot':
                    query_pred = model(support_images, support_fg_mask, support_bg_mask,
                                        query_images)
                    query_pred = query_pred.argmax(dim=1)
                else:
                    support_fts, query_fts = model(support_images, query_images)
                    support_fg_mask = torch.cat([torch.cat(way, dim=0) for way in support_fg_mask], dim=0) # [waysxshotsxB, H, W]

                    # get background feature and foreground feature from support image
                    support_fts_pos = torch.sum(support_fts * support_fg_mask.unsqueeze(1), dim=(-2, -1), keepdim=True) / (support_fg_mask.sum(dim=(-2, -1), keepdim=True) + 1e-8)
                    support_fts_neg = torch.sum(support_fts * (1-support_fg_mask).unsqueeze(1), dim=(-2, -1), keepdim=True) / ((1-support_fg_mask).sum(dim=(-2, -1), keepdim=True) + 1e-8)
                    
                    # choose the label according to the distance between foreground feature and background feature
                    query_pred = torch.zeros_like(query_labels)
                    query_pred[((query_fts - support_fts_pos)**2).sum(dim=1) < ((query_fts - support_fts_neg)**2).sum(dim=1)] = 1
                
                metric.record(np.array(query_pred[0].cpu()),
                            np.array(query_labels[0].cpu()),
                            labels=label_ids, n_run=run)
            
            classIoU, meanIoU = metric.get_mIoU(labels=sorted(labels), n_run=run)
            classIoU_binary, meanIoU_binary = metric.get_mIoU_binary(n_run=run)
            print(f'classIoU: {classIoU}')
            print(f'meanIoU: {meanIoU}')
            print(f'classIoU_binary: {classIoU_binary}')
            print(f'meanIoU_binary: {meanIoU_binary}')

    classIoU, classIoU_std, meanIoU, meanIoU_std = metric.get_mIoU(labels=sorted(labels))
    classIoU_binary, classIoU_std_binary, meanIoU_binary, meanIoU_std_binary = metric.get_mIoU_binary()

    print('----- Final Result -----')
    print(f'classIoU mean: {classIoU}')
    print(f'classIoU std: {classIoU_std}')
    print(f'meanIoU mean: {meanIoU}')
    print(f'meanIoU std: {meanIoU_std}')
    print(f'classIoU_binary mean: {classIoU_binary}')
    print(f'classIoU_binary std: {classIoU_std_binary}')
    print(f'meanIoU_binary mean: {meanIoU_binary}')
    print(f'meanIoU_binary std: {meanIoU_std_binary}')


if __name__ == '__main__':
    test()